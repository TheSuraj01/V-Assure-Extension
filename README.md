# V-Assure: Veeva Vault Test Automation Suite

V-Assure is an end-to-end solution designed to streamline the creation of test automation scripts for Veeva Vault. It consists of a high-performance Chrome Extension for recording UI interactions and a robust FastAPI backend that leverages Large Language Models (LLMs) and Retrieval-Augmented Generation (RAG) to generate precise, structured test scripts.

## 🚀 Features

- **Intelligent Recording:** Capture clicks, inputs, and navigations directly within Veeva Vault.
- **AI-Powered Generation:** Transform raw UI interactions into polished test scripts using state-of-the-art LLMs.
- **Enhanced RAG Engine:** Utilizes a custom Knowledge Base (KB) to ensure generated scripts follow established patterns and terminology.
- **Dynamic S3 Templates:** Seamless integration with S3-compatible storage for runtime loading and validation of Excel step-patterns without requiring application restarts.
- **Production-Grade Security:** AES-256-GCM encrypted configuration loading, strict origin-based CORS policies, and constant-time string comparisons for admin endpoints.
- **Optimized Logging:** Built-in redaction of sensitive credentials and keys to prevent data leaks.
- **Multi-Provider Support:** Seamlessly switch between LLM providers like **Groq**, **AWS Bedrock**, or **Local** servers via LiteLLM.
- **Real-time Streaming:** View script generation progress in real-time with Server-Sent Events (SSE).
- **Enterprise Docker Support:** Easy, secure deployment using security-hardened, multi-stage Docker builds running under non-root users.

---

## 🛠️ Project Structure

- **`client/`**: The Chrome Extension (Manifest v3) responsible for UI recording and interacting with the backend.
- **`server/`**: The FastAPI server handling the logic for script generation, RAG, encrypted configuration, and session management.
- **`docker-compose.yml`**: Orchestration for running the backend services in a containerized, production-ready environment.

---

## 🚦 Getting Started

### Prerequisites

- Python 3.11+
- Google Chrome Browser
- Docker & Docker Compose (optional, for containerized deployments)

### Backend Setup

1. Navigate to the backend directory:
   ```bash
   cd server
   ```
2. Create a virtual environment and install dependencies:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```
3. Generate your AES encryption key and encrypt your configuration secrets:
   ```bash
   # Generate a 64-hex-character AES key
   python -c "import os; print(os.urandom(32).hex())"
   
   # Set the key as an environment variable
   export CONFIG_ENCRYPTION_KEY="your_generated_hex_key"
   
   # Encrypt your secrets (requires a secrets.json with groq_api_key, etc.)
   python -m config.encrypted_config encrypt secrets.json config.enc
   ```
4. Start the server (Development mode):
   ```bash
   uvicorn main:app --reload
   ```

### Chrome Extension Setup

1. Open Chrome and navigate to `chrome://extensions/`.
2. Enable **Developer mode** in the top right corner.
3. Click **Load unpacked**.
4. Select the `client` folder from this repository.
5. The V-Assure icon should now appear in your browser's extension bar.

### Using Docker

To run the entire suite securely using Docker:
```bash
# Ensure CONFIG_ENCRYPTION_KEY is set in your environment
docker-compose up -d --build
```

---

## 📖 Usage

1. **Record:** Open Veeva Vault, click the V-Assure extension icon, and start recording your test flow.
2. **Generate:** Once finished, click "Generate Script". The extension will send the captured steps to the backend.
3. **Refine:** The backend uses RAG to compare your steps against the Knowledge Base for maximum accuracy.
4. **Export:** Download the final script as a structured TXT report or a raw JSON file for further processing.

---

## 🔧 Technical Stack

- **Frontend:** HTML5, CSS3, Vanilla JavaScript, Chrome Extension API (v3).
- **Backend:** Python, FastAPI, Pydantic, LiteLLM, Cryptography (AES-256-GCM).
- **AI/ML:** RAG (Custom implementation with TF-IDF/BM25), LLM Integration (Groq, Bedrock).
- **DevOps:** Docker, Docker Compose.

---

## 🛡️ License

This project is proprietary and confidential. Unauthorized copying or distribution is strictly prohibited.
