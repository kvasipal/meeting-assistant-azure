from app import app, socketio
import os

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    socketio.run(app, host='0.0.0.0', port=port) 