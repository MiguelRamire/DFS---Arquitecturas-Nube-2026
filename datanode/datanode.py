from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
import os, hashlib, httpx, threading, time

app = FastAPI(title="DataNode DFS")

# ── Configuración ─────────────────────────────────────────────────────────────
# Windows (desarrollo local): antes de correr, ejecutar en terminal:
#   set NODE_ID=datanode-1
#   set NODE_PORT=5001
#   set NODE_ADDR=localhost:5001
#   set NAMENODE_URL=http://localhost:5000
#   python -m uvicorn datanode:app --host 0.0.0.0 --port 5001
#
# Docker: las variables se inyectan desde docker-compose.yml

NODE_ID   = os.getenv("NODE_ID",   "datanode-1")
NODE_PORT = int(os.getenv("NODE_PORT", "5001"))
NODE_ADDR = os.getenv("NODE_ADDR", f"localhost:{NODE_PORT}")
NN_URL    = os.getenv("NAMENODE_URL", "http://localhost:5000")

# Carpeta de bloques: se crea automáticamente si no existe
BLOCKS_DIR = os.getenv("BLOCKS_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "blocks"))
os.makedirs(BLOCKS_DIR, exist_ok=True)

# ── Helpers ───────────────────────────────────────────────────────────────────

def block_path(block_id: str) -> str:
    return os.path.join(BLOCKS_DIR, f"{block_id}.block")

def list_local_blocks() -> list:
    return [f.replace(".block", "") for f in os.listdir(BLOCKS_DIR) if f.endswith(".block")]

def compute_checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def free_bytes() -> int:
    """Espacio libre en disco. Funciona en Windows, Linux y Mac."""
    try:
        stat = os.statvfs(BLOCKS_DIR)          # Linux / Mac
        return stat.f_bavail * stat.f_frsize
    except AttributeError:
        import shutil                           # Windows
        _, _, free = shutil.disk_usage(BLOCKS_DIR)
        return free

# ── Registro y heartbeat ──────────────────────────────────────────────────────

def register_with_namenode():
    payload = {"node_id": NODE_ID, "address": NODE_ADDR, "free_bytes": free_bytes()}
    for attempt in range(10):
        try:
            r = httpx.post(f"{NN_URL}/datanodes/register", json=payload, timeout=5)
            if r.status_code == 200:
                print(f"[{NODE_ID}] Registrado en NameNode correctamente.")
                return
        except Exception:
            pass
        print(f"[{NODE_ID}] Intento {attempt + 1}/10 — NameNode no disponible, reintentando en 3s...")
        time.sleep(3)
    print(f"[{NODE_ID}] No se pudo registrar en el NameNode.")

def heartbeat_loop():
    """Envía heartbeat cada 5 s con espacio libre y lista de bloques."""
    while True:
        time.sleep(5)
        try:
            httpx.post(
                f"{NN_URL}/datanodes/heartbeat",
                json={"node_id": NODE_ID, "free_bytes": free_bytes(), "block_ids": list_local_blocks()},
                timeout=3
            )
        except Exception:
            print(f"[{NODE_ID}] No se pudo enviar heartbeat.")

@app.on_event("startup")
def startup():
    threading.Thread(target=register_with_namenode, daemon=True).start()
    threading.Thread(target=heartbeat_loop,          daemon=True).start()

# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.put("/blocks/{block_id}")
async def store_block(block_id: str, request: Request, replicate_to: str = ""):
    """
    Recibe los bytes de un bloque en el body (application/octet-stream).
    Guarda en disco y replica al siguiente nodo en cadena si replicate_to está presente.

    Parámetros:
      block_id      (path)  : ID único del bloque
      replicate_to  (query) : nodos siguientes separados por coma, ej: "datanode-2:5001"
    Body: bytes crudos del bloque
    """
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="Body vacío — se esperaban los bytes del bloque")

    # Guardar en disco
    with open(block_path(block_id), "wb") as f:
        f.write(data)
    checksum = compute_checksum(data)
    print(f"[{NODE_ID}] Guardado: {block_id} ({len(data)} bytes) checksum={checksum[:8]}")

    # Replicar en cadena al siguiente nodo si corresponde
    if replicate_to:
        targets   = [t.strip() for t in replicate_to.split(",") if t.strip()]
        next_node = targets[0]
        remaining = ",".join(targets[1:])
        try:
            async with httpx.AsyncClient() as client:
                r = await client.put(
                    f"http://{next_node}/blocks/{block_id}",
                    content=data,
                    params={"replicate_to": remaining} if remaining else {},
                    headers={"Content-Type": "application/octet-stream"},
                    timeout=30
                )
            print(f"[{NODE_ID}] Replicado en {next_node} → {r.status_code}")
        except Exception as e:
            print(f"[{NODE_ID}] Error replicando en {next_node}: {e}")

    return {"status": "stored", "block_id": block_id, "checksum": checksum, "bytes": len(data)}

@app.get("/blocks/{block_id}")
def get_block(block_id: str):
    """Devuelve los bytes del bloque solicitado."""
    path = block_path(block_id)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"Bloque {block_id} no encontrado")
    print(f"[{NODE_ID}] Sirviendo: {block_id}")
    return FileResponse(path, media_type="application/octet-stream", filename=block_id)

@app.delete("/blocks/{block_id}")
def delete_block(block_id: str):
    """Elimina un bloque del disco local."""
    path = block_path(block_id)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Bloque no encontrado")
    os.remove(path)
    print(f"[{NODE_ID}] Eliminado: {block_id}")
    return {"status": "deleted", "block_id": block_id}

@app.get("/blocks")
def list_blocks():
    """Lista todos los bloques almacenados en este nodo."""
    return {
        "node_id": NODE_ID,
        "blocks": [
            {"block_id": bid, "size_bytes": os.path.getsize(block_path(bid))}
            for bid in list_local_blocks()
        ]
    }

@app.get("/health")
def health():
    return {"status": "alive", "node_id": NODE_ID,
            "blocks": len(list_local_blocks()), "free_bytes": free_bytes()}