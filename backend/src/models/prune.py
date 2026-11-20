from sqlalchemy import Column,Integer,String,JSON,Text,ForeignKey
from ..config.database import Base
from ..config.settings import CONFIG
class PruneTask(Base):
    __tablename__=CONFIG.get('prune_table','prune_tasks')
    id=Column(Integer,primary_key=True)
    source_id=Column(Integer,ForeignKey(CONFIG['source_table']+'.id'),nullable=False)
    name=Column(String(160),nullable=False)
    target_from=Column(String(120),nullable=False)
    target_to=Column(String(120),nullable=False)
    parameters=Column(JSON,nullable=False)
    state=Column(String(20),default='queued')
    input_file=Column(String(200),default='')
    input_sha256=Column(String(64),default='')
    input_count=Column(Integer,default=0)
    progress=Column(JSON,nullable=False,default=dict)
    result=Column(JSON,nullable=True)
    error=Column(Text,default='')
    seq=Column(Integer,default=0)
    heartbeat=Column(String(40),default='')
    created_at=Column(String(40),nullable=False)
    updated_at=Column(String(40),nullable=False)
