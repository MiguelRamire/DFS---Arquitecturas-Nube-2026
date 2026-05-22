from fastapi import FastAPI, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import Dict, List
import time
import math
import hashlib
import threading
import os
import jwt

from jwt.exceptions import InvalidTokenError

app = FastAPI(title="NameNode DFS")

security = HTTPBearer()

# ─────────────────────────────────────────────────────────────
# Configuración
# ─────────────────────────────────────────────────────────────

REPLICATION_FACTOR = int(os.getenv("REPLICATION_FACTOR", "2"))

BLOCK_SIZE = int(
    os.getenv("CHUNK_SIZE_MB", "64")
) * 1024 * 1024

HEARTBEAT_TIMEOUT = int(
    os.getenv("HEARTBEAT_TIMEOUT", "15")
)

JWT_SECRET = os.getenv(
    "JWT_SECRET",
    "dfs-secret-2025"
)

JWT_ALGORITHM = "HS256"

# ─────────────────────────────────────────────────────────────
# Usuarios
# ─────────────────────────────────────────────────────────────

USERS = {
    "alice": "password123",
    "bob": "password456",
    "admin": "admin"
}

# ─────────────────────────────────────────────────────────────
# Estructuras en memoria
# ─────────────────────────────────────────────────────────────

files_metadata: Dict[str, dict] = {}

datanodes: Dict[str, dict] = {}

directories = set(["/"])

# ─────────────────────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    node_id: str
    address: str
    free_bytes: int


class HeartbeatRequest(BaseModel):
    node_id: str
    free_bytes: int
    block_ids: List[str]


class UploadPlanRequest(BaseModel):
    filename: str
    file_size: int


class CommitRequest(BaseModel):
    filename: str
    block_ids: List[str]


class DirRequest(BaseModel):
    path: str


# ─────────────────────────────────────────────────────────────
# JWT
# ─────────────────────────────────────────────────────────────

def create_token(username: str):

    payload = {
        "sub": username,
        "exp": int(time.time()) + 3600
    }

    token = jwt.encode(
        payload,
        JWT_SECRET,
        algorithm=JWT_ALGORITHM
    )

    return token


def verify_token(
    credentials: HTTPAuthorizationCredentials = Depends(security)
):

    try:

        payload = jwt.decode(
            credentials.credentials,
            JWT_SECRET,
            algorithms=[JWT_ALGORITHM]
        )

        username = payload.get("sub")

        if not username:
            raise HTTPException(
                status_code=401,
                detail="Token inválido"
            )

        return username

    except InvalidTokenError:

        raise HTTPException(
            status_code=401,
            detail="Token inválido o expirado"
        )


# ─────────────────────────────────────────────────────────────
# Auth
# ─────────────────────────────────────────────────────────────

@app.post("/auth/login")
def login(req: LoginRequest):

    if req.username not in USERS:

        raise HTTPException(
            status_code=401,
            detail="Usuario incorrecto"
        )

    if USERS[req.username] != req.password:

        raise HTTPException(
            status_code=401,
            detail="Contraseña incorrecta"
        )

    token = create_token(req.username)

    return {
        "token": token,
        "expires_in": 3600,
        "username": req.username
    }


# ─────────────────────────────────────────────────────────────
# DataNodes
# ─────────────────────────────────────────────────────────────

@app.post("/datanodes/register")
def register_datanode(req: RegisterRequest):

    datanodes[req.node_id] = {
        "address": req.address,
        "free_bytes": req.free_bytes,
        "last_heartbeat": time.time(),
        "block_count": 0
    }

    print(
        f"[NameNode] DataNode registrado: "
        f"{req.node_id} @ {req.address}"
    )

    return {
        "status": "registered",
        "node_id": req.node_id
    }


@app.post("/datanodes/heartbeat")
def heartbeat(req: HeartbeatRequest):

    if req.node_id not in datanodes:

        raise HTTPException(
            status_code=404,
            detail="DataNode no registrado"
        )

    datanodes[req.node_id]["last_heartbeat"] = time.time()

    datanodes[req.node_id]["free_bytes"] = req.free_bytes

    datanodes[req.node_id]["block_count"] = len(
        req.block_ids
    )

    return {"status": "ok"}


@app.get("/datanodes/status")
def cluster_status():

    now = time.time()

    result = {}

    for nid, info in datanodes.items():

        alive = (
            now - info["last_heartbeat"]
        ) < HEARTBEAT_TIMEOUT

        result[nid] = {
            "address": info["address"],
            "alive": alive,
            "last_heartbeat_ago": round(
                now - info["last_heartbeat"],
                1
            ),
            "block_count": info.get(
                "block_count",
                0
            ),
            "free_bytes": info.get(
                "free_bytes",
                0
            )
        }

    return result


# ─────────────────────────────────────────────────────────────
# Utilidades
# ─────────────────────────────────────────────────────────────

def get_active_nodes():

    now = time.time()

    return {
        nid: info
        for nid, info in datanodes.items()
        if now - info["last_heartbeat"]
        < HEARTBEAT_TIMEOUT
    }


def select_nodes_for_block(
    exclude: List[str] = []
):

    active = get_active_nodes()

    candidates = [
        (nid, info)
        for nid, info in active.items()
        if nid not in exclude
    ]

    if len(candidates) < REPLICATION_FACTOR:

        raise HTTPException(
            status_code=507,
            detail=(
                f"Se necesitan "
                f"{REPLICATION_FACTOR} nodos activos"
            )
        )

    candidates.sort(
        key=lambda x: x[1].get(
            "block_count",
            0
        )
    )

    return [
        nid
        for nid, _ in candidates[:REPLICATION_FACTOR]
    ]


def generate_block_id(
    filename: str,
    index: int
):

    raw = f"{filename}:{index}:{time.time()}"

    return hashlib.sha256(
        raw.encode()
    ).hexdigest()[:16]


# ─────────────────────────────────────────────────────────────
# Files
# ─────────────────────────────────────────────────────────────

@app.post("/files/upload_plan")
def upload_plan(
    req: UploadPlanRequest,
    username: str = Depends(verify_token)
):

    key = f"{username}/{req.filename}"

    if key in files_metadata:

        raise HTTPException(
            status_code=409,
            detail="El archivo ya existe"
        )

    num_blocks = math.ceil(
        req.file_size / BLOCK_SIZE
    )

    blocks_plan = []

    for i in range(num_blocks):

        block_id = generate_block_id(
            req.filename,
            i
        )

        assigned_nodes = select_nodes_for_block()

        last_size = (
            req.file_size % BLOCK_SIZE
            or BLOCK_SIZE
        )

        block_size = (
            BLOCK_SIZE
            if i < num_blocks - 1
            else last_size
        )

        blocks_plan.append({
            "block_id": block_id,
            "index": i,
            "size": block_size,
            "primary": assigned_nodes[0],
            "replicas": assigned_nodes[1:],
            "nodes": assigned_nodes
        })

    files_metadata[key] = {
        "status": "pending",
        "file_size": req.file_size,
        "owner": username,
        "blocks": blocks_plan
    }

    return {
        "filename": req.filename,
        "num_blocks": num_blocks,
        "blocks": blocks_plan
    }


@app.post("/files/commit")
def commit_file(
    req: CommitRequest,
    username: str = Depends(verify_token)
):

    key = f"{username}/{req.filename}"

    if key not in files_metadata:

        raise HTTPException(
            status_code=404,
            detail="Archivo no encontrado"
        )

    files_metadata[key]["status"] = "committed"

    return {
        "status": "committed",
        "filename": req.filename
    }


@app.get("/files")
def list_files(
    username: str = Depends(verify_token)
):

    result = []

    for key, meta in files_metadata.items():

        if (
            meta["owner"] == username
            and meta["status"] == "committed"
        ):

            result.append({
                "filename": key.split("/", 1)[1],
                "size": meta["file_size"],
                "blocks": len(meta["blocks"]),
                "status": meta["status"]
            })

    return {"files": result}


@app.get("/files/{filename}/locate")
def locate_file(
    filename: str,
    username: str = Depends(verify_token)
):

    key = f"{username}/{filename}"

    if key not in files_metadata:

        raise HTTPException(
            status_code=404,
            detail="Archivo no encontrado"
        )

    fmeta = files_metadata[key]
    
    if fmeta["status"] != "committed":

        raise HTTPException(
            status_code=409,
            detail="El archivo aún no está completamente subido"
        )

    active = get_active_nodes()

    active = get_active_nodes()

    result = []

    for block in fmeta["blocks"]:

        available = [
            n
            for n in block["nodes"]
            if n in active
        ]

        if not available:

            raise HTTPException(
                status_code=503,
                detail=(
                    f"Bloque "
                    f"{block['block_id']} "
                    f"no disponible"
                )
            )

        result.append({
            "block_id": block["block_id"],
            "index": block["index"],
            "size": block["size"],
            "address": datanodes[
                available[0]
            ]["address"]
        })

    return {
        "filename": filename,
        "blocks": result
    }


@app.delete("/files/{filename}")
def delete_file(
    filename: str,
    username: str = Depends(verify_token)
):

    key = f"{username}/{filename}"

    if key not in files_metadata:

        raise HTTPException(
            status_code=404,
            detail="Archivo no encontrado"
        )

    del files_metadata[key]

    return {
        "status": "deleted",
        "filename": filename
    }


# ─────────────────────────────────────────────────────────────
# Directories
# ─────────────────────────────────────────────────────────────

@app.post("/dirs/mkdir")
def mkdir(
    req: DirRequest,
    username: str = Depends(verify_token)
):

    clean_path = req.path.strip("/")

    full_path = f"{username}/{clean_path}"

    if full_path in directories:

        raise HTTPException(
            status_code=409,
            detail="El directorio ya existe"
        )

    directories.add(full_path)

    return {
        "status": "created",
        "path": req.path
    }


@app.delete("/dirs/rmdir/{path:path}")
def rmdir(
    path: str,
    username: str = Depends(verify_token)
):

    clean_path = path.strip("/")

    full_path = f"{username}/{clean_path}"

    if full_path not in directories:

        raise HTTPException(
            status_code=404,
            detail="Directorio no encontrado"
        )

    has_files = any(
        key.startswith(f"{username}/{clean_path}/")
        for key in files_metadata
    )

    if has_files:

        raise HTTPException(
            status_code=409,
            detail="El directorio no está vacío"
        )

    directories.remove(full_path)

    return {
        "status": "deleted",
        "path": path
    }


# ─────────────────────────────────────────────────────────────
# Health
# ─────────────────────────────────────────────────────────────

@app.get("/health")
def health():

    return {
        "status": "alive",
        "role": "namenode"
    }


# ─────────────────────────────────────────────────────────────
# Re-replicación
# ─────────────────────────────────────────────────────────────

def replication_check():

    while True:

        time.sleep(10)

        active = get_active_nodes()

        for key, fmeta in files_metadata.items():

            for block in fmeta.get("blocks", []):

                alive_replicas = [
                    n
                    for n in block["nodes"]
                    if n in active
                ]

                if (
                    0 < len(alive_replicas)
                    < REPLICATION_FACTOR
                ):

                    print(
                        f"[NameNode] "
                        f"Re-replicando "
                        f"bloque "
                        f"{block['block_id']}"
                    )

                    try:

                        new_nodes = select_nodes_for_block(
                            exclude=alive_replicas
                        )

                        block["nodes"] = (
                            alive_replicas
                            + new_nodes
                        )

                    except Exception as e:

                        print(
                            f"[NameNode] "
                            f"Error re-replicando: {e}"
                        )


threading.Thread(
    target=replication_check,
    daemon=True
).start()