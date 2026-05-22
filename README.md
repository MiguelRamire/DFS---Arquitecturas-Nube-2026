# DFS — Sistema de Archivos Distribuidos por Bloques

Sistema de archivos distribuido minimalista inspirado en HDFS/GFS, desarrollado para la materia **Arquitecturas de Nube y Sistemas Distribuidos** — UPB 2026.

Permite subir y descargar archivos grandes dividiéndolos en bloques y distribuyéndolos entre múltiples nodos (DataNodes), con un nodo central de metadatos (NameNode) y replicación mínima de 2 copias por bloque.

---

## Estructura del proyecto

```
dfs-project/
├── namenode/
│   ├── namenode.py          # Servidor central de metadatos + autenticación JWT
│   ├── requirements.txt     # fastapi, uvicorn, pydantic, PyJWT
│   └── Dockerfile
├── datanode/
│   ├── datanode.py          # Almacenamiento de bloques + heartbeat + replicación
│   ├── requirements.txt     # fastapi, uvicorn, httpx
│   └── Dockerfile
├── client/
│   ├── client.py            # CLI: login, put, get, ls, rm, mkdir, rmdir, status
│   └── requirements.txt     # httpx
├── docker-compose.yml       # Orquesta todo el clúster
├── .gitignore
└── README.md
```

---

## Requisitos previos

- Python 3.11 o superior → https://www.python.org/downloads/
- Docker Desktop (para correr con Docker) → https://www.docker.com/products/docker-desktop/
- Git → https://git-scm.com/

Verificar que estén instalados:

```bash
python --version
docker --version
git --version
```

---

## Opción A — Correr en local (Windows, sin Docker)

Esta opción es la más rápida para desarrollo y pruebas.
Se abren **4 terminales** en VS Code (una por componente).

### Paso 1 — Clonar el repositorio

```bash
git clone https://github.com/tu-usuario/dfs-project.git
cd dfs-project
```

### Paso 2 — Instalar dependencias del NameNode

```bash
cd namenode
pip install -r requirements.txt
```

Dependencias que instala:
- `fastapi` — framework REST
- `uvicorn` — servidor ASGI
- `pydantic` — validación de datos
- `PyJWT` — generación y verificación de tokens JWT

### Paso 3 — Arrancar el NameNode (Terminal 1)

```bash
cd namenode
python -m uvicorn namenode:app --host 0.0.0.0 --port 5000
```

Verificar que funciona:
```bash
# En otra terminal (o en el navegador)
curl http://localhost:5000/health
# Respuesta esperada: {"status":"alive","role":"namenode",...}
```

La documentación interactiva de la API queda en: http://localhost:5000/docs

### Paso 4 — Instalar dependencias del DataNode

```bash
cd datanode
pip install -r requirements.txt
```

Dependencias que instala:
- `fastapi` + `uvicorn` — igual que el NameNode
- `httpx` — cliente HTTP para registrarse y enviar heartbeats al NameNode

### Paso 5 — Arrancar DataNode 1 (Terminal 2)

En Windows se usan `set` para las variables de entorno:

```bash
cd datanode

set NODE_ID=datanode-1
set NODE_PORT=5001
set NODE_ADDR=localhost:5001
set NAMENODE_URL=http://localhost:5000

python -m uvicorn datanode:app --host 0.0.0.0 --port 5001
```

Deberías ver en la consola:
```
[datanode-1] Registrado en NameNode correctamente.
```

### Paso 6 — Arrancar DataNode 2 (Terminal 3)

```bash
cd datanode

set NODE_ID=datanode-2
set NODE_PORT=5002
set NODE_ADDR=localhost:5002
set NAMENODE_URL=http://localhost:5000

python -m uvicorn datanode:app --host 0.0.0.0 --port 5002
```

### Paso 7 — Instalar dependencias del cliente

```bash
cd client
pip install -r requirements.txt
```

### Paso 8 — Usar el cliente (Terminal 4)

```bash
cd client
set NAMENODE_URL=http://localhost:5000
```

#### Iniciar sesión

```bash
python client.py login alice password123
```

Usuarios disponibles por defecto:
| Usuario | Contraseña |
|---------|-----------|
| alice   | password123 |
| bob     | password456 |
| admin   | admin |

#### Verificar estado del clúster

```bash
python client.py status
```

Respuesta esperada:
```
Nodo                  Vivo  Bloques   Libre (MB)    Últ. HB
------------------------------------------------------------
datanode-1            True        0      50000.0         2s
datanode-2            True        0      50000.0         1s
```

#### Subir un archivo

```bash
# Crear archivo de prueba (1 MB)
python -c "open('test.bin','wb').write(b'x'*1024*1024)"

# Subirlo al DFS
python client.py put test.bin
```

#### Listar archivos

```bash
python client.py ls
```

#### Descargar un archivo

```bash
python client.py get test.bin test_descargado.bin
```

#### Verificar integridad (los hashes deben ser iguales)

```bash
# PowerShell
Get-FileHash test.bin -Algorithm MD5
Get-FileHash test_descargado.bin -Algorithm MD5

# O con Python
python -c "import hashlib; print(hashlib.md5(open('test.bin','rb').read()).hexdigest())"
python -c "import hashlib; print(hashlib.md5(open('test_descargado.bin','rb').read()).hexdigest())"
```

#### Crear y eliminar directorios

```bash
python client.py mkdir documentos
python client.py rmdir documentos
```

#### Eliminar un archivo

```bash
python client.py rm test.bin
python client.py ls
# test.bin ya no debe aparecer
```

---

## Opción B — Correr con Docker Compose

Esta opción levanta todo el clúster con un solo comando.
Requiere Docker Desktop instalado y corriendo.

### Paso 1 — Construir y levantar el clúster

```bash
# Desde la raíz del proyecto
docker compose up --build
```

Esto construye las imágenes y arranca: NameNode + DataNode 1 + DataNode 2 + DataNode 3 + Cliente.

Verificar que todos los contenedores están corriendo:

```bash
docker compose ps
```

### Paso 2 — Usar el cliente dentro del contenedor

```bash
# Iniciar sesión
docker exec dfs-client python client.py login alice password123

# Ver estado del clúster
docker exec dfs-client python client.py status

# Subir un archivo (debe estar en la carpeta uploads/ del proyecto)
docker exec dfs-client python client.py put /uploads/test.bin

# Listar archivos
docker exec dfs-client python client.py ls

# Descargar
docker exec dfs-client python client.py get test.bin /downloads/test_recuperado.bin

# Verificar integridad
docker exec dfs-client python -c "
import hashlib
h1 = hashlib.md5(open('/uploads/test.bin','rb').read()).hexdigest()
h2 = hashlib.md5(open('/downloads/test_recuperado.bin','rb').read()).hexdigest()
print('Original: ', h1)
print('Recuperado:', h2)
print('Integridad OK:', h1 == h2)
"
```

### Paso 3 — Probar tolerancia a fallos

```bash
# Ver estado inicial
docker exec dfs-client python client.py status

# Simular caída de datanode-2
docker stop datanode-2

# Esperar 15 segundos (timeout del heartbeat)
# Verificar que el NameNode lo detectó
docker exec dfs-client python client.py status
# datanode-2 debe aparecer como: Vivo = False

# El archivo debe seguir siendo descargable gracias a la réplica en datanode-1
docker exec dfs-client python client.py get test.bin /downloads/test_failover.bin

# Restaurar el nodo
docker start datanode-2
# Esperar 10 segundos y verificar que volvió a alive = True
docker exec dfs-client python client.py status
```

### Paso 4 — Ver logs de un nodo específico

```bash
docker logs namenode
docker logs datanode-1
docker logs datanode-2
```

### Paso 5 — Apagar el clúster

```bash
docker compose down
# Para también borrar los volúmenes (bloques guardados):
docker compose down -v
```

---

## API REST — Referencia rápida

Una vez corriendo, la documentación interactiva está disponible en:
- NameNode: http://localhost:5000/docs
- DataNode 1: http://localhost:5001/docs
- DataNode 2: http://localhost:5002/docs

### Endpoints del NameNode

| Método | Endpoint | Auth | Descripción |
|--------|----------|------|-------------|
| POST | `/auth/login` | No | Autenticación. Retorna token JWT |
| POST | `/files/upload_plan` | Sí | Solicita plan de bloques antes de subir |
| POST | `/files/commit` | Sí | Confirma que todos los bloques fueron subidos |
| GET | `/files/{nombre}/locate` | Sí | Retorna ubicación de bloques para descargar |
| GET | `/files` | Sí | Lista archivos del usuario autenticado |
| DELETE | `/files/{nombre}` | Sí | Elimina un archivo |
| POST | `/dirs/mkdir` | Sí | Crea un directorio |
| DELETE | `/dirs/rmdir/{ruta}` | Sí | Elimina un directorio vacío |
| POST | `/datanodes/register` | No | Un DataNode se registra al arrancar |
| POST | `/datanodes/heartbeat` | No | Heartbeat periódico de un DataNode |
| GET | `/datanodes/status` | No | Estado del clúster |
| GET | `/health` | No | Health check |

### Endpoints del DataNode

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| PUT | `/blocks/{id}` | Recibe y almacena un bloque (body: bytes) |
| GET | `/blocks/{id}` | Devuelve los bytes de un bloque |
| DELETE | `/blocks/{id}` | Elimina un bloque |
| GET | `/blocks` | Lista todos los bloques del nodo |
| GET | `/health` | Health check |

---

## Solución de problemas comunes

### `AttributeError: module 'os' has no attribute 'statvfs'`

Este error ocurre en Windows. Está corregido en la versión actual del `datanode.py`.
Si lo ves, asegúrate de tener la versión más reciente:

```bash
git pull
```

### El DataNode no puede conectarse al NameNode

Verificar que el NameNode esté corriendo:
```bash
curl http://localhost:5000/health
```

Verificar que la variable `NAMENODE_URL` esté bien configurada:
```bash
# Windows
echo %NAMENODE_URL%

# Si está vacía, configurarla:
set NAMENODE_URL=http://localhost:5000
```

### `Token inválido o expirado`

El token dura 1 hora. Volver a iniciar sesión:
```bash
python client.py login alice password123
```

### `507 Insufficient Storage — Se necesitan 2 DataNodes activos`

El NameNode necesita al menos 2 DataNodes registrados y activos para almacenar un bloque con replicación. Verificar que ambos DataNodes estén corriendo:

```bash
python client.py status
```

---

## Usuarios disponibles

| Usuario | Contraseña | Nota |
|---------|-----------|------|
| alice   | password123 | Usuario de prueba |
| bob     | password456 | Usuario de prueba |
| admin   | admin | Administrador |

Para agregar usuarios, editar el diccionario `USERS` en `namenode/namenode.py`.

---

## Tecnologías usadas

| Componente | Tecnología | Por qué |
|-----------|-----------|---------|
| Lenguaje | Python 3.11 | Rápido de prototipar, ecosistema maduro |
| Framework HTTP | FastAPI | Async, Swagger automático, validación con Pydantic |
| Servidor | Uvicorn | ASGI, compatible con FastAPI async |
| Cliente HTTP | httpx | Soporte async/sync, compatible con Windows |
| Autenticación | PyJWT | Sin estado, estándar de la industria |
| Orquestación | Docker Compose v3.8 | Simple, reproducible |

---

## Integrantes

| Persona | Componente | Responsabilidad |
|---------|-----------|----------------|
| Persona 1 | NameNode | namenode.py + JWT + mkdir/rmdir + secciones 1 y 3 del informe grupal |
| Persona 2 | DataNode | datanode.py + replicación en cadena + secciones 5 y 6 del informe grupal |
| Persona 3 | Cliente + Docker | client.py + docker-compose.yml + pruebas + secciones 4 y 7 del informe grupal |

---

## Repositorio y gestión de tareas

- Tablero Trello: [enlace al tablero]
- Repositorio GitHub: [enlace al repo]
