from pydantic import BaseModel,Field
class PruneInput(BaseModel):
    source_id:int
    name:str=Field(min_length=2,max_length=160)
    target_from:str=Field(min_length=1,max_length=120)
    target_to:str=Field(min_length=1,max_length=120)
    parameters:dict={}
class PruneResume(BaseModel):
    time_limit_sec:int|None=None
