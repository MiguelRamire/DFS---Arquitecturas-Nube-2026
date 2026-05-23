import httpx
import os
import sys

NN_URL = os.getenv("NAMENODE_URL", "http://namenode:5000")
BLOCK_SIZE = 64 * 1024 * 1024

TOKEN = None


def login():

    global TOKEN

    print("=== DFS LOGIN ===")

    username = input("Usuario: ")
    password = input("Contraseña: ")

    r = httpx.post(
        f"{NN_URL}/auth/login",
        json={
            "username": username,
            "password": password
        },
        timeout=10
    )

    if r.status_code != 200:
        print("Credenciales inválidas")
        sys.exit(1)

    TOKEN = r.json()["token"]

    print("Login exitoso\n")


def get_datanode_address(node_id: str) -> str:
    r = httpx.get(f"{NN_URL}/datanodes/status", timeout=5)
    nodes = r.json()

    if node_id in nodes:
        return nodes[node_id]["address"]

    return f"{node_id}:5001"


def put(local_path: str):
    if not os.path.exists(local_path):
        print(f"Archivo '{local_path}' no encontrado")
        return

    filename = os.path.basename(local_path)
    file_size = os.path.getsize(local_path)

    print(f"Subiendo '{filename}'...")

    r = httpx.post(
        f"{NN_URL}/files/upload_plan",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={
            "filename": filename,
            "file_size": file_size
        },
        timeout=10
    )

    if r.status_code != 200:
        print(f"Error: {r.text}")
        return

    plan = r.json()
    blocks_plan = plan["blocks"]

    uploaded_ids = []

    with open(local_path, "rb") as f:
        for block_info in blocks_plan:
            block_id = block_info["block_id"]
            primary = block_info["primary"]
            replicas = block_info["replicas"]

            dn_addr = get_datanode_address(primary)

            chunk_data = f.read(BLOCK_SIZE)

            replicate_to = ",".join([
                get_datanode_address(r) for r in replicas
            ])


            params = {}

            if replicate_to:
                params["replicate_to"] = replicate_to

            resp = httpx.put(
                f"http://{dn_addr}/blocks/{block_id}",
                content=chunk_data,
                headers={
                    "Content-Type": "application/octet-stream"
                },
                params=params,
                timeout=60
            )

            if resp.status_code != 200:
                print(f"Error subiendo bloque: {resp.text}")
                return

            uploaded_ids.append(block_id)
            print(f"Bloque {block_id} subido")

    r = httpx.post(
        f"{NN_URL}/files/commit",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={
            "filename": filename,
            "block_ids": uploaded_ids
        },
        timeout=10
    )

    if r.status_code == 200:
        print(f"Archivo '{filename}' subido correctamente")
    else:
        print(f"Error commit: {r.text}")


def get(filename: str, local_dest: str):
    r = httpx.get(
        f"{NN_URL}/files/{filename}/locate",
        headers={"Authorization": f"Bearer {TOKEN}"},
        timeout=10
    )

    if r.status_code != 200:
        print(f"Error: {r.text}")
        return

    blocks = sorted(r.json()["blocks"], key=lambda b: b["index"])

    with open(local_dest, "wb") as out:
        for block in blocks:
            block_id = block["block_id"]
            address = block["address"]

            resp = httpx.get(
                f"http://{address}/blocks/{block_id}",
                timeout=30
            )

            if resp.status_code != 200:
                print(f"Error descargando bloque")
                return

            out.write(resp.content)

    print(f"Archivo descargado en '{local_dest}'")


def ls():
    r = httpx.get(
        f"{NN_URL}/files",
        headers={"Authorization": f"Bearer {TOKEN}"},
        timeout=10
    )

    if r.status_code != 200:
        print(f"Error: {r.text}")
        return

    files = r.json()["files"]

    if not files:
        print("No hay archivos")
        return

    print(f"{'Archivo':<30} {'Bloques':<10} {'Estado':<15}")
    print("-" * 60)

    for f in files:
        print(f"{f['filename']:<30} {f['blocks']:<10} {f['status']:<15}")


def rm(filename: str):
    r = httpx.delete(
        f"{NN_URL}/files/{filename}",
        headers={"Authorization": f"Bearer {TOKEN}"},
        timeout=10
    )

    if r.status_code == 200:
        print(f"Archivo '{filename}' eliminado")
    else:
        print(f"Error: {r.text}")


def status():
    r = httpx.get(f"{NN_URL}/datanodes/status", timeout=10)

    if r.status_code != 200:
        print(f"Error: {r.text}")
        return

    nodes = r.json()

    print(f"{'Nodo':<20} {'Vivo':<10} {'Bloques':<10}")
    print("-" * 50)

    for nid, info in nodes.items():
        print(f"{nid:<20} {str(info['alive']):<10} {info['block_count']:<10}")


def print_usage():
    print("""
Uso:

python client.py put <archivo>
python client.py get <archivo_dfs> <destino>
python client.py ls
python client.py rm <archivo>
python client.py status
""")


if __name__ == "__main__":

    if len(sys.argv) < 2:
        print_usage()
        sys.exit(1)

    login()

    cmd = sys.argv[1]

    if cmd == "put" and len(sys.argv) == 3:
        put(sys.argv[2])

    elif cmd == "get" and len(sys.argv) == 4:
        get(sys.argv[2], sys.argv[3])

    elif cmd == "ls":
        ls()

    elif cmd == "rm" and len(sys.argv) == 3:
        rm(sys.argv[2])

    elif cmd == "status":
        status()

    else:
        print_usage()