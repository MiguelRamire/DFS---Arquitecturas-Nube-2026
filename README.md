# DFS — Sistema de Archivos Distribuidos por Bloques

Sistema de archivos distribuido inspirado en HDFS/GFS, desarrollado para la materia
**Arquitecturas de Nube y Sistemas Distribuidos — UPB 2026**.

El sistema divide archivos en bloques, los replica en múltiples DataNodes y mantiene
los metadatos en un NameNode central. Incluye tolerancia a fallos, autenticación JWT
y un cliente CLI completo.

---

## Arquitectura general

| Componente | Rol |
|---|---|
| **NameNode** | Servidor central de metadatos, autenticación, ubicación de bloques y estado del clúster |
| **DataNodes** | Almacenan bloques físicos, envían heartbeats y replican datos en cadena |
| **Cliente** | CLI para interactuar con el DFS (`login`, `put`, `get`, `ls`, `rm`, `mkdir`, `rmdir`, `status`) |
| **Docker Compose** | Orquesta todo el sistema con un solo comando |

---

## Requisitos

- Docker Desktop instalado y **corriendo** → https://www.docker.com/products/docker-desktop/
- Git → https://git-scm.com/
- Windows / Linux / macOS

Verificar instalación:

```bash
docker --version
git --version
```

---

## IMPORTANTE — Todos los comandos del cliente requieren `-it`

El cliente pide usuario y contraseña de forma interactiva al ejecutarse.
Por eso **todos los comandos con `docker exec` deben incluir `-it`**, sin excepción.

Sin `-it`, Docker no asigna entrada interactiva y el comando falla con:
```
EOFError: EOF when reading a line
```

**Forma correcta — siempre con `-it`:**
```bash
docker exec -it dfs-client python client.py put /uploads/archivo.txt
```

**Forma incorrecta — falla:**
```bash
docker exec dfs-client python client.py put /uploads/archivo.txt
```

Esto aplica a `put`, `get`, `ls`, `rm`, `mkdir`, `rmdir` y `status`.
El sistema está diseñado así para garantizar que cada operación sea autenticada.

---

## Ejecutar el sistema

### Paso 1 — Clonar el repositorio

```bash
git clone git clone https://github.com/MiguelRamire/DFS---Arquitecturas-Nube-2026.git
cd DFS---Arquitecturas-Nube-2026
```

### Paso 2 — Crear las carpetas de archivos

```bash
mkdir uploads downloads
```

- `uploads/` → aquí van los archivos que se van a subir al DFS
- `downloads/` → aquí aparecen los archivos descargados desde el DFS

### Paso 3 — Levantar el clúster

```bash
docker compose up --build
```

Esto construye las imágenes y arranca: NameNode + DataNode 1 + DataNode 2 + DataNode 3 + Cliente.

Verificar que todos los contenedores estén corriendo:

```bash
docker compose ps
```

Todos deben aparecer con estado `running`.

---

## Pruebas del sistema paso a paso

### Prueba 1 — Verificar estado del clúster

```bash
curl http://localhost:5000/datanodes/status
```

Resultado esperado: los tres DataNodes con `"alive": true`.

---

### Prueba 2 — Subir un archivo pequeño (1 MB)

**Crear el archivo de prueba:**

Windows (PowerShell):
```powershell
fsutil file createnew uploads/test_1mb.bin 1048576
```

Linux / Mac:
```bash
dd if=/dev/zero of=uploads/test_1mb.bin bs=1M count=1
```

**Subir al DFS** (el sistema pedirá usuario y contraseña):

```bash
docker exec -it dfs-client python client.py put /uploads/test_1mb.bin
```

Cuando aparezca el prompt:
```
=== DFS LOGIN ===
Usuario: alice
Contraseña: password123
```

El sistema autentica, divide el archivo en bloques y los distribuye entre los DataNodes.

---

### Prueba 3 — Listar archivos

```bash
docker exec -it dfs-client python client.py ls
```

Debe aparecer `test_1mb.bin` con su tamaño y número de bloques.

---

### Prueba 4 — Descargar y verificar integridad

**Descargar el archivo:**

```bash
docker exec -it dfs-client python client.py get test_1mb.bin /uploads/downloads/test_1mb_recuperado.bin
```

**Verificar que el archivo descargado es idéntico al original (MD5):**

Windows (PowerShell):
```powershell
certutil -hashfile uploads/test_1mb.bin MD5
certutil -hashfile uploads/downloads/test_1mb_recuperado.bin MD5
```

Linux / Mac:
```bash
md5sum uploads/test_1mb.bin
md5sum uploads/downloads/test_1mb_recuperado.bin
```

Los dos hashes deben ser exactamente iguales. Si coinciden, la integridad está garantizada.

---

### Prueba 5 — Tolerancia a fallos (caída de un DataNode)

**Ver estado inicial:**

```bash
curl http://localhost:5000/datanodes/status
```

**Simular caída del DataNode 2:**

```bash
docker stop datanode-2
```

**Esperar unos segundos y consultar el estado:**

```bash
curl http://localhost:5000/datanodes/status
```

`datanode-2` debe aparecer con `"alive": false`.

**Intentar descargar el archivo con el nodo caído:**

```bash
docker exec -it dfs-client python client.py get test_1mb.bin /uploads/downloads/test_failover.bin
```
El archivo sigue siendo accesible gracias a la réplica en los otros DataNodes.
Verificar integridad nuevamente con MD5 — debe coincidir.

**Verificación de integridad**

```bash
certutil -hashfile uploads/test_1mb.bin MD5
certutil -hashfile uploads/downloads/test_failover.bin MD5
```

**Restaurar el DataNode:**

```bash
docker start datanode-2
```

Después de unos segundos vuelve a aparecer como `"alive": true`.

---

### Prueba 6 — Archivo grande (200 MB, múltiples bloques)

**Crear archivo de 200 MB:**

Windows (PowerShell):
```powershell
fsutil file createnew uploads/test_200mb.bin 209715200
```

Linux / Mac:
```bash
dd if=/dev/zero of=uploads/test_200mb.bin bs=1M count=200
```

**Subir al DFS:**

```bash
docker exec -it dfs-client python client.py put /uploads/test_200mb.bin
```

El sistema lo divide automáticamente en múltiples bloques de 64 MB y los distribuye entre los DataNodes.

**Descargar y verificar integridad:**

```bash
docker exec -it dfs-client python client.py get test_200mb.bin /uploads/downloads/test_200mb_recuperado.bin
```

Windows:
```powershell
certutil -hashfile uploads/test_200mb.bin MD5
certutil -hashfile uploads/downloads/test_200mb_recuperado.bin MD5
```
---

---

### Prueba 7 — Eliminar un archivo

```bash
docker exec -it dfs-client python client.py rm test_1mb.bin
docker exec -it dfs-client python client.py ls
```

`test_1mb.bin` ya no debe aparecer en la lista.

---

### Prueba 8 — Ver estado del clúster desde el cliente

```bash
docker exec -it dfs-client python client.py status
```

Muestra una tabla con todos los DataNodes, si están vivos, cuántos bloques tienen y cuánto espacio libre.

---

## Apagar el sistema

```bash
# Apagar los contenedores (los datos se conservan)
docker compose down

# Apagar y borrar todos los datos almacenados
docker compose down -v
```

---

## Usuarios disponibles

| Usuario | Contraseña  |
|---------|-------------|
| alice   | password123 |
| bob     | password456 |
| admin   | admin       |

Para agregar usuarios, editar el diccionario `USERS` en `namenode/namenode.py`.

---

## API REST — Referencia rápida

La documentación interactiva (Swagger) está disponible mientras el sistema corre:

- NameNode: http://localhost:5000/docs
- DataNode 1: http://localhost:5001/docs
- DataNode 2: http://localhost:5002/docs
- DataNode 3: http://localhost:5003/docs

### Endpoints del NameNode

| Método | Endpoint | Auth | Descripción |
|--------|----------|------|-------------|
| POST | `/auth/login` | No | Autenticación. Retorna token JWT |
| POST | `/files/upload_plan` | Sí | Plan de bloques antes de subir |
| POST | `/files/commit` | Sí | Confirma que todos los bloques fueron subidos |
| GET | `/files/{nombre}/locate` | Sí | Ubicación de bloques para descargar |
| GET | `/files` | Sí | Lista archivos del usuario autenticado |
| DELETE | `/files/{nombre}` | Sí | Elimina un archivo |
| POST | `/dirs/mkdir` | Sí | Crea un directorio |
| DELETE | `/dirs/rmdir/{ruta}` | Sí | Elimina un directorio vacío |
| POST | `/datanodes/register` | No | Registro de un DataNode al arrancar |
| POST | `/datanodes/heartbeat` | No | Heartbeat periódico del DataNode |
| GET | `/datanodes/status` | No | Estado del clúster |
| GET | `/health` | No | Health check |

### Endpoints del DataNode

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| PUT | `/blocks/{id}` | Recibe y almacena un bloque (bytes) |
| GET | `/blocks/{id}` | Devuelve los bytes de un bloque |
| DELETE | `/blocks/{id}` | Elimina un bloque |
| GET | `/blocks` | Lista todos los bloques del nodo |
| GET | `/health` | Health check |

---

## Solución de problemas comunes

### `EOFError: EOF when reading a line`

Falta el `-it` en el comando. Todos los comandos del cliente requieren modo interactivo porque piden usuario y contraseña. Solución:

```bash
# Agregar -it siempre
docker exec -it dfs-client python client.py <comando>
```

### `507 Insufficient Storage — Se necesitan 2 DataNodes activos`

El NameNode necesita mínimo 2 DataNodes activos para garantizar la replicación. Verificar:

```bash
docker compose ps
curl http://localhost:5000/datanodes/status
```

### El DataNode no aparece como activo

Puede que no haya terminado de registrarse. Esperar 10 segundos y volver a consultar el estado. Si sigue sin aparecer, revisar los logs:

```bash
docker logs datanode-1
```

### Ver logs de cualquier componente

```bash
docker logs namenode
docker logs datanode-1
docker logs datanode-2
docker logs datanode-3
docker logs dfs-client
```

---

## Tecnologías usadas

| Componente | Tecnología | Por qué |
|-----------|-----------|---------|
| Lenguaje | Python 3.11 | Ecosistema maduro, fácil de prototipar |
| Framework HTTP | FastAPI | Async, Swagger automático, validación con Pydantic |
| Servidor | Uvicorn | ASGI, compatible con FastAPI |
| Cliente HTTP | httpx | Soporte async/sync, compatible con Windows |
| Autenticación | PyJWT | Sin estado, estándar de la industria |
| Orquestación | Docker Compose v3.8 | Simple y reproducible |

---

## Despliegue en AWS Academy

> **Sección pendiente — por completar por Juan José Ramírez Zuluaga**

Esta sección describe cómo desplegar el sistema en máquinas virtuales EC2 de AWS Academy,
de modo que los nodos se comuniquen por Internet en vez de por red local Docker.

Los puntos a documentar son:

- Configuración de instancias EC2 (tipo, región, AMI usada)
- Instalación de Docker en cada VM
- Configuración de Security Groups (puertos a abrir por nodo)
- Variables de entorno a cambiar para apuntar a IPs públicas o privadas de la VPC
- Comandos para levantar cada nodo en su VM correspondiente
- Verificación de que los nodos se comunican correctamente por Internet

---

## Integrantes

| Integrante | Componente |
|-----------|-----------|
| Juan Felipe Cano Noreña | NameNode + AWS |
| Miguel Ángel Ramírez Velásquez | DataNode |
| Juan José Ramírez Zuluaga | Cliente |

---

## Gestión del proyecto

- Tablero Trello: https://trello.com/b/2UHm3jxM/dfs-arquitecturas-nube-2026
- Repositorio GitHub: https://github.com/MiguelRamire/DFS---Arquitecturas-Nube-2026
