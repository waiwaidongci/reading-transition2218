import json,time,logging
from redis import Redis
from sqlalchemy import select
from .config.database import Session
from .config.settings import REDIS_URL,ARTIFACT_DIR
from .models.run import Run
from .models import source,record,user,note,audit,prune
from .services.algorithm import calculate
from .services.audit import log
from .services.export import csv_response
from .services import prune as prune_service
queue=Redis.from_url(REDIS_URL)
ARTIFACT_DIR.mkdir(parents=True,exist_ok=True)
def execute(id):
    with Session() as db:
        r=db.get(Run,id)
        if not r or r.state!='queued':return
        r.state='running';db.commit()
        try:
            result=calculate(r.snapshot['records'],r.parameters)
            json.dumps(result,allow_nan=False)
            r.result=result;r.state='completed'
            response=csv_response(result,'run.csv')
            (ARTIFACT_DIR/f'run-{r.id}.csv').write_bytes(response.body)
            log(db,'worker','run.completed',r.id)
        except Exception as e:
            logging.exception('analysis failed');r.state='failed';r.error=str(e)[:2000]
            log(db,'worker','run.failed',r.id,r.error)
        db.commit()
def main():
    while True:
        (ARTIFACT_DIR/'heartbeat').write_text(str(time.time()))
        try:prune_service.recover_stale()
        except Exception:logging.exception('prune stale recovery failed')
        value=queue.blpop(['analysis:queue','prune:queue'],timeout=2)
        if not value:continue
        key=value[0].decode() if isinstance(value[0],bytes) else value[0]
        if key=='prune:queue':prune_service.execute(int(value[1]))
        else:execute(int(value[1]))
if __name__=='__main__':main()
