# Axiom Engine: End-to-End Operational Guide

Welcome to the **Axiom Engine**, a decentralized AI network designed to autonomously discover, verify, and archive objective truth. This guide provides step-by-step instructions for launching a local 3-node cluster, interacting with the ledger, and testing the system.

---

## 🚀 1. Quick Start (Prerequisites)

Ensure you have the following installed:
- **Python 3.9+**
- **uv** (Recommended for dependency management: `curl -LsSf https://astral.sh/uv/install.sh | sh`)
- **PyQt6** (Required for the Chat Client GUI)

### Installation
First, sync the environment and install all dependencies:
```bash
# Sync project and install dependencies
uv sync --all-extras

# Install the spaCy language model
python -m spacy download en_core_web_lg
```

---

## 🕸️ 2. Launching the Three-Node Cluster

To simulate a decentralized network locally, you need to launch three instances of the Axiom Node. Each node requires its own **P2P Port**, **API Port**, and **Database File**.

### Node 1: The Bootstrap Node
This node acts as the entry point for other peers.
```bash
uv run axiom_server --p2p-port 5001 --api-port 8001 --db-name node1.db
```

### Node 2: The First Peer
This node connects to Node 1 to join the network.
```bash
uv run axiom_server --p2p-port 5002 --api-port 8002 --db-name node2.db --bootstrap-peer http://127.0.0.1:5001
```

### Node 3: The Second Peer
This node also joins the network via Node 1.
```bash
uv run axiom_server --p2p-port 5003 --api-port 8003 --db-name node3.db --bootstrap-peer http://127.0.0.1:5001
```

> [!TIP]
> Each node will start two main components:
> 1. **P2P Layer**: Communicates with other nodes to synchronize the blockchain.
> 2. **Flask API**: Provides endpoints for the Chat Client to query facts.

---

## 🔍 3. Viewing the Database (Ledger)

Axiom uses **SQLite** to store its ledger. You can inspect the data using the `sqlite3` command-line tool or a GUI like [DB Browser for SQLite](https://sqlitebrowser.org/).

### Useful SQL Queries
Open a database file:
```bash
sqlite3 node1.db
```

List all discovered facts:
```sql
SELECT content, status, score FROM facts;
```

Check the blockchain height:
```sql
SELECT height, hash, timestamp FROM blockchain ORDER BY height DESC LIMIT 5;
```

View fact sources:
```sql
SELECT * FROM source;
```

---

## 💬 4. Launching the Chat Client

The **Axiom Client** is a GUI application that allows you to ask the network questions and receive verified answers from the ledger.

### Run the Client
```bash
uv run axiom_client
```

### Advanced: Connecting to a Specific Node
By default, the client connects to `http://127.0.0.1:8001`. To talk to a different node (e.g., Node 2), set the `AXIOM_API_URL` environment variable:
```bash
export AXIOM_API_URL=http://127.0.0.1:8002
uv run axiom_client
```

---

## 🛠️ 5. Common Terminal Commands

### Development & Maintenance
- **Run Tests**: `uv run pytest`
- **Lint Code**: `uv run ruff check .`
- **Type Checking**: `uv run mypy`
- **Run Static Analysis**: `./check.sh`

### Node Configuration Options
Run `uv run axiom_server --help` to see all available flags:
- `--host`: Bind to a specific IP (default: `127.0.0.1`).
- `--p2p-port`: Port for peer-to-peer syncing.
- `--api-port`: Port for the client API.
- `--bootstrap-peer`: URL of an existing peer to connect to.
- `--db-name`: Path to the SQLite database.

---

## 🌐 6. Using the Web Client (index.html)

Axiom includes a modern web-based chat interface located at `docs/index.html`. This client is designed to work both locally and via **GitHub Pages**.

### Running Locally
1. Ensure at least one Axiom node is running (e.g., Node 1 on port 8001).
2. Simply open `docs/index.html` in your web browser.
3. The client will automatically attempt to connect to `http://127.0.0.1:8001` (fallback).

### Deploying to GitHub Pages
Axiom is pre-configured for GitHub Pages:
1. Go to your repository settings on GitHub.
2. Navigate to **Pages**.
3. Under **Build and deployment**, set the source to `Deploy from a branch`.
4. Select the `main` branch and the `/docs` folder.
5. Once deployed, anyone can access your fact network via the generated `github.io` URL.

> [!IMPORTANT]
> The web client requires an accessible API node. If your nodes are running behind a firewall or on `localhost`, the GitHub Pages site will only be able to talk to them if you are accessing it from the same machine or have configured a tunnel (like `ngrok`).

---

## ❓ Troubleshooting

| Issue | Solution |
| :--- | :--- |
| **Port Conflict** | Ensure no other process is using ports 5001-5003 or 8001-8003. Use `lsof -i :8001` to check. |
| **Nodes Not Syncing** | Check if the `--bootstrap-peer` URL matches the IP/P2P port of Node 1. |
| **Missing Dependencies** | Run `uv sync --all-extras` again and ensure your virtual environment is active. |
| **SSL Errors** | If using HTTPS, ensure certificates in the `ssl/` directory are valid. |

---

*“Veritas per Axioma — Truth through Axiom.”*
