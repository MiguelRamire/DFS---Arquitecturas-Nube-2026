from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
import os
import hashlib
import httpx
import threading
import time

app = FastAPI(title="DataNode DFS")

# ─── Configuración desde variables de entorno ────────────────────────────────
# Estas variables se pasan desde docker-compose.yml
NODE_ID   = os.getenv("NODE_ID", "datanode-1")       # nombre único de este nodo
NODE_PORT = int(os.getenv("NODE_PORT", "5001"))        # puerto donde escucha
NODE_ADDR = os.getenv("NODE_ADDR", f"{NODE_ID}:{NODE_PORT}")  # dirección anunciada al NameNode
NN_URL    = os.getenv("NAMENODE_URL", "http://namenode:5000")  # URL del NameNode

# Carpeta donde se guardan los bloques en disco
BLOCKS_DIR = "/data/blocks"
os.makedirs(BLOCKS_DIR, exist_ok=True)

# ─── Funciones auxiliares ────────────────────────────────────────────────────
def block_path(block_id: str) -> str:
    """Retorna la ruta completa en disco donde se guarda un bloque."""
    return os.path.join(BLOCKS_DIR, f"{block_id}.block")

def list_local_blocks() -> list:
    """Lista todos los bloques actualmente almacenados en este nodo."""
    return [f.replace(".block", "") for f in os.listdir(BLOCKS_DIR) if f.endswith(".block")]

def compute_checksum(data: bytes) -> str:
    """Calcula el hash SHA-256 de un bloque para verificar integridad."""
    return hashlib.sha256(data).hexdigest()

def free_bytes() -> int:
    """Retorna los bytes libres en disco (compatible con Windows y Linux)."""
    try:
        # Linux / Unix
        stat = os.statvfs(BLOCKS_DIR)
        return stat.f_bavail * stat.f_frsize
    except AttributeError:
        # Windows
        import shutil
        total, used, free = shutil.disk_usage(BLOCKS_DIR)
        return free

# ─── Registro y heartbeat al NameNode ────────────────────────────────────────
def register_with_namenode():
    """
    Al arrancar, el DataNode se registra en el NameNode para que
    este sepa que existe y pueda asignarle bloques.
    """
    payload = {
        "node_id": NODE_ID,
        "address": NODE_ADDR,
        "free_bytes": free_bytes()
    }
    for attempt in range(10):
        try:
            r = httpx.post(f"{NN_URL}/datanodes/register", json=payload, timeout=5)
            if r.status_code == 200:
                print(f"[{NODE_ID}] Registrado en NameNode correctamente.")
                return
        except Exception:
            pass
        print(f"[{NODE_ID}] Intento {attempt+1}: NameNode no disponible aún, reintentando en 3s...")
        time.sleep(3)
    print(f"[{NODE_ID}] No se pudo registrar en el NameNode.")

def heartbeat_loop():
    """
    Envía un heartbeat al NameNode cada 5 segundos con:
    - espacio libre en disco
    - lista actual de bloques almacenados
    Esto permite al NameNode detectar si este nodo cae (si deja de recibir heartbeats).
    """
    while True:
        time.sleep(5)
        try:
            payload = {
                "node_id": NODE_ID,
                "free_bytes": free_bytes(),
                "block_ids": list_local_blocks()
            }
            httpx.post(f"{NN_URL}/datanodes/heartbeat", json=payload, timeout=3)
        except Exception:
            print(f"[{NODE_ID}] No se pudo enviar heartbeat al NameNode.")

# Arrancar registro y heartbeat en hilos de fondo al iniciar
@app.on_event("startup")
def startup():
    threading.Thread(target=register_with_namenode, daemon=True).start()
    threading.Thread(target=heartbeat_loop, daemon=True).start()

# ─── Endpoints ───────────────────────────────────────────────────────────────

@app.put("/blocks/{block_id}")
async def store_block(block_id: str, file: UploadFile = File(...), replicate_to: str = ""):
    """
    Recibe un bloque del cliente (o de otro DataNode durante replicación).
    Lo guarda en disco y lo replica al siguiente nodo si 'replicate_to' no está vacío.

    replicate_to: dirección del siguiente DataNode en la cadena, ej: "datanode-2:5001"
    """
    data = await file.read()

    # Guardar bloque en disco
    path = block_path(block_id)
    with open(path, "wb") as f:
        f.write(data)

    checksum = compute_checksum(data)
    print(f"[{NODE_ID}] Bloque almacenado: {block_id} ({len(data)} bytes) checksum={checksum[:8]}")

    # Si hay más nodos en la cadena de replicación, reenviar al siguiente
    if replicate_to:
        targets = replicate_to.split(",")
        next_node = targets[0].strip()
        remaining = ",".join(targets[1:])
        try:
            async with httpx.AsyncClient() as client:
                files = {"file": (block_id, data, "application/octet-stream")}
                params = {"replicate_to": remaining} if remaining else {}
                r = await client.put(
                    f"http://{next_node}/blocks/{block_id}",
                    files=files,
                    params=params,
                    timeout=30
                )
                print(f"[{NODE_ID}] Bloque {block_id} replicado en {next_node} → status {r.status_code}")
        except Exception as e:
            print(f"[{NODE_ID}] Error al replicar {block_id} en {next_node}: {e}")

    return {"status": "stored", "block_id": block_id, "checksum": checksum, "bytes": len(data)}

@app.get("/blocks/{block_id}")
def get_block(block_id: str):
    """
    Devuelve el contenido binario de un bloque al cliente que lo solicite.
    Verifica integridad con checksum antes de enviar.
    """
    path = block_path(block_id)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"Bloque {block_id} no encontrado en este nodo")

    # Verificar integridad
    with open(path, "rb") as f:
        data = f.read()
    checksum = compute_checksum(data)
    print(f"[{NODE_ID}] Sirviendo bloque {block_id} checksum={checksum[:8]}")

    return FileResponse(path, media_type="application/octet-stream", filename=block_id)

@app.delete("/blocks/{block_id}")
def delete_block(block_id: str):
    """Elimina un bloque del disco local."""
    path = block_path(block_id)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Bloque no encontrado")
    os.remove(path)
    print(f"[{NODE_ID}] Bloque eliminado: {block_id}")
    return {"status": "deleted", "block_id": block_id}

@app.get("/blocks")
def list_blocks():
    """Lista todos los bloques almacenados en este nodo con su tamaño."""
    blocks = []
    for block_id in list_local_blocks():
        path = block_path(block_id)
        blocks.append({
            "block_id": block_id,
            "size_bytes": os.path.getsize(path)
        })
    return {"node_id": NODE_ID, "blocks": blocks}

@app.get("/health")
def health():
    return {"status": "alive", "node_id": NODE_ID, "blocks": len(list_local_blocks()), "free_bytes": free_bytes()}
