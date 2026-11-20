import csv,io
from fastapi import APIRouter,Depends,HTTPException
from fastapi.responses import Response
from ..config.database import db_session
from ..schemas.prune import PruneInput,PruneResume
from ..services.auth import current_user,writer
from ..controllers.prune import PruneController as C
router=APIRouter()
@router.get('/prune-tasks')
def tasks(db=Depends(db_session),u=Depends(current_user)):return C.list(db)
@router.post('/prune-tasks')
def create(data:PruneInput,db=Depends(db_session),u=Depends(writer)):return C.create(db,u,data)
@router.get('/prune-tasks/{id}')
def detail(id:int,db=Depends(db_session),u=Depends(current_user)):return C.get(db,id)
@router.post('/prune-tasks/{id}/resume')
def resume(id:int,data:PruneResume=PruneResume(),db=Depends(db_session),u=Depends(writer)):return C.resume(db,u,id,data)
@router.get('/prune-tasks/{id}/export')
def export(id:int,db=Depends(db_session),u=Depends(current_user)):
    t=C.get(db,id)
    sessions=(t.get('result') or {}).get('removed_sessions')
    if not sessions:raise HTTPException(409,'暂无可导出的剔除方案')
    buf=io.StringIO();w=csv.writer(buf);w.writerow(['session','path'])
    for r in sessions:w.writerow([r['session'],r['path']])
    return Response('﻿'+buf.getvalue(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="prune-'+str(id)+'-removal.csv"'})
