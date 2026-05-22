from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Dict, List, Optional
import time
import math
import hashlib
import threading

app = FastAPI(title="NameNode DFS")

# ─── Estructuras de datos en memoria ────────────────────────────────────────
# Esta tabla guarda: nombre_archivo → lista ordenada de bloques
# Cada bloque tiene: block_id, tamaño, y en qué DataNodes está
files_metadata: Dict[str, dict] = {}

# Esta tabla guarda qué DataNodes están registrados y cuándo fue su último heartbeat
datanodes: Dict[str, dict] = {}

# Factor de replicación mínimo requerido
REPLICATION_FACTOR = 2

# Tamaño de bloque por defecto: 64 MB
BLOCK_SIZE = 64 * 1024 * 1024

# Tiempo máximo sin heartbeat antes de considerar un nodo caído (segundos)
HEARTBEAT_TIMEOUT = 15

# ─── Modelos de datos ────────────────────────────────────────────────────────
class RegisterRequest(BaseModel):
    node_id: str      # identificador único del DataNode, ej: "datanode-1"
    address: str      # dirección IP:puerto donde escucha, ej: "datanode-1:5001"
    free_bytes: int   # espacio libre en bytes

class HeartbeatRequest(BaseModel):
    node_id: str
    free_bytes: int
    block_ids: List[str]   # lista de bloques que tiene este nodo ahora mismo

class UploadPlanRequest(BaseModel):
    filename: str
    file_size: int   # tamaño total del archivo en bytes

class CommitRequest(BaseModel):
    filename: str
    block_ids: List[str]   # lista ordenada de IDs de bloque que conforman el archivo

# ─── Funciones auxiliares ────────────────────────────────────────────────────
def get_active_nodes():
    """Retorna solo los DataNodes que han enviado heartbeat recientemente."""
    now = time.time()
    return {
        nid: info for nid, info in datanodes.items()
        if now - info["last_heartbeat"] < HEARTBEAT_TIMEOUT
    }

def select_nodes_for_block(exclude: List[str] = []) -> List[str]:
    """
    Selecciona R DataNodes para almacenar un bloque.
    Criterio: nodos activos, no excluidos, ordenados por menor carga (menos bloques).
    """
    active = get_active_nodes()
    candidates = [
        (nid, info) for nid, info in active.items()
        if nid not in exclude
    ]
    if len(candidates) < REPLICATION_FACTOR:
        raise HTTPException(
            status_code=507,
            detail=f"No hay suficientes DataNodes activos. Se necesitan {REPLICATION_FACTOR}, hay {len(candidates)}"
        )
    # Ordenar por número de bloques almacenados (menor carga primero)
    candidates.sort(key=lambda x: x[1].get("block_count", 0))
    selected = [nid for nid, _ in candidates[:REPLICATION_FACTOR]]
    return selected

def generate_block_id(filename: str, index: int) -> str:
    """Genera un ID único para un bloque basado en nombre de archivo e índice."""
    raw = f"{filename}:{index}:{time.time()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]

# ─── Tarea periódica de re-replicación ──────────────────────────────────────
def replication_check():
    """
    Tarea que corre en segundo plano cada 10 segundos.
    Verifica si algún bloque tiene menos réplicas de las requeridas
    y ordena re-replicación si es necesario.
    """
    while True:
        time.sleep(10)
        active = get_active_nodes()
        for filename, fmeta in files_metadata.items():
            for block in fmeta.get("blocks", []):
                # Filtrar réplicas que aún están en nodos activos
                alive_replicas = [n for n in block["nodes"] if n in active]
                if len(alive_replicas) < REPLICATION_FACTOR and len(alive_replicas) > 0:
                    print(f"[NameNode] Bloque {block['block_id']} tiene solo {len(alive_replicas)} réplica(s). Iniciando re-replicación.")
                    # Seleccionar destino que no tenga ya el bloque
                    try:
                        new_nodes = select_nodes_for_block(exclude=alive_replicas)
                        block["nodes"] = alive_replicas + new_nodes
                        print(f"[NameNode] Re-replicación: {block['block_id']} → {block['nodes']}")
                    except Exception as e:
                        print(f"[NameNode] No se pudo re-replicar {block['block_id']}: {e}")

# Iniciar hilo de re-replicación al arrancar
threading.Thread(target=replication_check, daemon=True).start()

# ─── Endpoints ───────────────────────────────────────────────────────────────

@app.post("/datanodes/register")
def register_datanode(req: RegisterRequest):
    """
    Un DataNode se registra aquí cuando arranca.
    Guarda su dirección y marca su primer heartbeat.
    """
    datanodes[req.node_id] = {
        "address": req.address,
        "free_bytes": req.free_bytes,
        "last_heartbeat": time.time(),
        "block_count": 0
    }
    print(f"[NameNode] DataNode registrado: {req.node_id} @ {req.address}")
    return {"status": "registered", "node_id": req.node_id}

@app.post("/datanodes/heartbeat")
def heartbeat(req: HeartbeatRequest):
    """
    Recibe el heartbeat periódico de un DataNode.
    Actualiza timestamp, espacio libre y cantidad de bloques.
    """
    if req.node_id not in datanodes:
        raise HTTPException(status_code=404, detail="DataNode no registrado")
    datanodes[req.node_id]["last_heartbeat"] = time.time()
    datanodes[req.node_id]["free_bytes"] = req.free_bytes
    datanodes[req.node_id]["block_count"] = len(req.block_ids)
    return {"status": "ok"}

@app.get("/datanodes/status")
def cluster_status():
    """
    Devuelve el estado actual de todos los DataNodes.
    Útil para monitoreo y depuración.
    """
    now = time.time()
    result = {}
    for nid, info in datanodes.items():
        result[nid] = {
            "address": info["address"],
            "alive": (now - info["last_heartbeat"]) < HEARTBEAT_TIMEOUT,
            "last_heartbeat_ago": round(now - info["last_heartbeat"], 1),
            "block_count": info.get("block_count", 0),
            "free_bytes": info.get("free_bytes", 0)
        }
    return result

@app.post("/files/upload_plan")
def upload_plan(req: UploadPlanRequest):
    """
    El cliente llama esto antes de subir un archivo.
    El NameNode calcula cuántos bloques se necesitan y asigna DataNodes para cada uno.
    Retorna un plan completo: lista de bloques con su ID y los DataNodes asignados.
    """
    if req.filename in files_metadata:
        raise HTTPException(status_code=409, detail="El archivo ya existe")

    num_blocks = math.ceil(req.file_size / BLOCK_SIZE)
    blocks_plan = []

    for i in range(num_blocks):
        block_id = generate_block_id(req.filename, i)
        assigned_nodes = select_nodes_for_block()
        block_size = BLOCK_SIZE if i < num_blocks - 1 else (req.file_size % BLOCK_SIZE or BLOCK_SIZE)
        blocks_plan.append({
            "block_id": block_id,
            "index": i,
            "size": block_size,
            "primary": assigned_nodes[0],
            "replicas": assigned_nodes[1:],
            "nodes": assigned_nodes
        })

    # Guardar plan temporal (se confirma con /commit)
    files_metadata[req.filename] = {
        "status": "pending",
        "file_size": req.file_size,
        "blocks": blocks_plan
    }
    return {"filename": req.filename, "num_blocks": num_blocks, "blocks": blocks_plan}

@app.post("/files/commit")
def commit_file(req: CommitRequest):
    """
    El cliente llama esto después de haber subido todos los bloques exitosamente.
    Cambia el estado del archivo de 'pending' a 'committed'.
    """
    if req.filename not in files_metadata:
        raise HTTPException(status_code=404, detail="Archivo no encontrado en metadatos")
    files_metadata[req.filename]["status"] = "committed"
    print(f"[NameNode] Archivo committed: {req.filename}")
    return {"status": "committed", "filename": req.filename}

@app.get("/files/{filename}/locate")
def locate_file(filename: str):
    """
    El cliente llama esto para saber dónde están los bloques de un archivo.
    Retorna la lista de bloques con la dirección del DataNode donde leerlos.
    """
    if filename not in files_metadata:
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    fmeta = files_metadata[filename]
    if fmeta["status"] != "committed":
        raise HTTPException(status_code=409, detail="El archivo no está completamente subido aún")

    active = get_active_nodes()
    result_blocks = []
    for block in fmeta["blocks"]:
        # Buscar una réplica que esté en un nodo activo
        available_nodes = [n for n in block["nodes"] if n in active]
        if not available_nodes:
            raise HTTPException(
                status_code=503,
                detail=f"Bloque {block['block_id']} no disponible: todos los DataNodes con esta réplica están caídos"
            )
        result_blocks.append({
            "block_id": block["block_id"],
            "index": block["index"],
            "size": block["size"],
            "address": datanodes[available_nodes[0]]["address"]
        })
    return {"filename": filename, "blocks": result_blocks}

@app.get("/files")
def list_files():
    """Lista todos los archivos committed en el sistema."""
    return {
        "files": [
            {"filename": fname, "size": meta["file_size"], "blocks": len(meta["blocks"]), "status": meta["status"]}
            for fname, meta in files_metadata.items()
        ]
    }

@app.delete("/files/{filename}")
def delete_file(filename: str):
    """Elimina un archivo del namespace (los bloques se borran en los DataNodes por separado)."""
    if filename not in files_metadata:
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    del files_metadata[filename]
    return {"status": "deleted", "filename": filename}

@app.get("/health")
def health():
    return {"status": "alive", "role": "namenode"}
