from ..services import prune
from ..utils.serialization import row
class PruneController:
    list=staticmethod(prune.list_tasks)
    create=staticmethod(prune.create_task)
    @staticmethod
    def get(db,id):return prune.task_detail(prune.get_task(db,id))
    @staticmethod
    def resume(db,user,id,data):return prune.resume_task(db,user,id,data.time_limit_sec)
