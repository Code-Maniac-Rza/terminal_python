from flask import Flask, send_from_directory, request
from flask_socketio import SocketIO, emit
from flask_cors import CORS
import subprocess
import os
import sys
import threading
import queue
from threading import Lock
import logging

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, resources={
    r"/*": {
        "origins": "*",
        "allow_headers": "*",
        "expose_headers": "*",
        "methods": ["GET", "POST", "OPTIONS"]
    }
})
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    logger=True,
    engineio_logger=True,
    ping_timeout=60,
    ping_interval=25,
    async_mode='threading'
)

# Store subprocess instances and their output queues
subprocess_map = {}
output_queues = {}
subprocess_locks = {}

def get_static_dir():
    """Get the absolute path to the static directory and create it if it doesn't exist."""
    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
    os.makedirs(static_dir, exist_ok=True)
    return static_dir

app.static_folder = get_static_dir()

def output_reader(proc, queue, sid):
    """Continuously read output from the subprocess."""
    logger.debug(f"Starting output reader for session {sid}")
    try:
        while True:
            output = proc.stdout.readline()
            if output:
                logger.debug(f"Output for {sid}: {output.strip()}")
                queue.put(output)
                socketio.emit('console_output', output, room=sid)
            elif proc.poll() is not None:
                logger.debug(f"Process ended for session {sid}")
                break
    except Exception as e:
        logger.error(f"Error in output reader for {sid}: {str(e)}")
        queue.put(f"Error reading output: {str(e)}\n")

@app.route('/')
def index():
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/static/<path:filename>')
def serve_static(filename):
    return send_from_directory(app.static_folder, filename)

@socketio.on('connection_establish')
def handle_connection(data):
    try:
        sid = request.sid
        logger.info(f"Client connected: {sid}")
        
        # Create new subprocess
        proc = subprocess.Popen(
            [sys.executable, 'run.py'],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # Redirect stderr to stdout
            text=True,
            bufsize=1,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        
        logger.debug(f"Started subprocess for {sid}")
        
        # Set up queue and start output reader thread
        output_queue = queue.Queue()
        subprocess_map[sid] = proc
        output_queues[sid] = output_queue
        subprocess_locks[sid] = Lock()
        
        # Start output reader thread
        threading.Thread(
            target=output_reader,
            args=(proc, output_queue, sid),
            daemon=True
        ).start()
        
        emit('console_output', "Welcome to the Expense Tracker!\n")
        
    except Exception as e:
        logger.error(f"Error establishing connection: {str(e)}")
        emit('console_output', f"Error establishing connection: {str(e)}\n")

@socketio.on('command_entered')
def handle_command(command):
    sid = request.sid
    logger.debug(f"Received command from {sid}: {command}")
    
    if sid not in subprocess_map:
        logger.error(f"No active session found for {sid}")
        emit('console_output', "Error: No active session found. Please refresh the page.\n")
        return

    proc = subprocess_map[sid]
    lock = subprocess_locks[sid]

    try:
        with lock:
            if proc.poll() is not None:
                logger.error(f"Session expired for {sid}")
                emit('console_output', "Error: Session expired. Please refresh the page.\n")
                return

            # Write command to subprocess
            proc.stdin.write(command + '\n')
            proc.stdin.flush()
            logger.debug(f"Command sent to subprocess for {sid}")

    except Exception as e:
        logger.error(f"Error processing command for {sid}: {str(e)}")
        emit('console_output', f"Error processing command: {str(e)}\n")

@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    logger.info(f"Client {sid} disconnected")
    
    if sid in subprocess_map:
        proc = subprocess_map.pop(sid)
        if proc:
            proc.terminate()
            proc.wait()
    
    if sid in output_queues:
        del output_queues[sid]
    
    if sid in subprocess_locks:
        del subprocess_locks[sid]

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"Starting server on port {port}")
    socketio.run(app, host='0.0.0.0', port=port, debug=True)