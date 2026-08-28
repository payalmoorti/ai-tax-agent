import hashlib,json,logging
from pathlib import Path
from pydantic import BaseModel
def stable_id(*parts:str,prefix:str="") -> str:
    value="|".join(parts).encode("utf-8")
    return prefix+hashlib.sha256(value).hexdigest()
def write_json(path:Path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    payload=value.model_dump(mode="json") if isinstance(value,BaseModel) else value
    path.write_text(json.dumps(payload,indent=2,ensure_ascii=False,default=str),encoding="utf-8")
def read_json(path:Path): return json.loads(path.read_text(encoding="utf-8"))
def configure_logging(level="INFO"):
    logging.basicConfig(level=level,format="%(asctime)s %(levelname)s %(name)s %(message)s")
def safe_name(name:str): return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
