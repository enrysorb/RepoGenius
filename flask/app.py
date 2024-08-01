from flask import Flask, render_template, request, jsonify, abort, session
import requests
import os
import json
import pika
import re
import uuid
from flask_socketio import SocketIO, emit, join_room

app = Flask(__name__)
socketio = SocketIO(app)

# Generate or load secret key
def generate_secret_key():
    secret_key_file = 'secret_key.txt'
    if os.path.exists(secret_key_file):
        with open(secret_key_file, 'r') as file:
            secret_key = file.read().strip()
    else:
        secret_key = os.urandom(24).hex()
        with open(secret_key_file, 'w') as file:
            file.write(secret_key)
    return secret_key

app.secret_key = generate_secret_key()

# Function to find repositories on GitHub
def trova_repositoriesfixed(linguaggio='', risultati_per_pagina=10, pagine=1, topic=''):
    repositories = []
    for pagina in range(1, pagine + 1):
        query_parts = []
        if linguaggio:
            query_parts.append(f"language:{linguaggio}")
        if topic:
            query_parts.append(f"topic:{topic}")
        
        query = '+'.join(query_parts)
        url = f"https://api.github.com/search/repositories?q={query}&sort=stars"
        
        if risultati_per_pagina:
            url += f"&per_page={risultati_per_pagina}"
        
        url += f"&page={pagina}"

        risposta = requests.get(url)
        if risposta.status_code == 200:
            dati = risposta.json()
            repositories.extend(dati['items'])
        else:
            print(f"Errore: {risposta.status_code}")
            return []
    
    return repositories

# Function to create a file with repositories
def crea_file_repository(repositories):
    directory = "repositories"
    os.makedirs(directory, exist_ok=True)
    
    file_path = os.path.join(directory, "repositories.json")
    link_repository_list = [repo['html_url'] for repo in repositories]
    with open(file_path, 'w') as file:
        json.dump({"repositories": link_repository_list}, file, indent=4)

# Function to add a single repository link
def aggiungi_repository(link):
    directory = "repositories"
    os.makedirs(directory, exist_ok=True)
    
    file_path = os.path.join(directory, "repositories.json")
    link_repository_list = [link]
    with open(file_path, 'w') as file:
        json.dump({"repositories": link_repository_list}, file, indent=4)

# Function to send repositories to RabbitMQ
def invia_repositories(client_id):
    directory = "repositories"
    file_path = os.path.join(directory, "repositories.json")
    if not os.path.exists(file_path):
        return {"status": "error", "message": "Il file repositories.json non esiste."}

    if os.path.getsize(file_path) == 0:
        return {"status": "error", "message": "Il file repositories.json è vuoto."}

    with open(file_path, 'r') as file:
        try:
            dati = json.load(file)
        except json.JSONDecodeError:
            return {"status": "error", "message": "Errore nel decodificare il file JSON."}

    repositories = dati['repositories']

    connection = pika.BlockingConnection(pika.ConnectionParameters(os.environ['RABBITMQ_HOST']))
    channel = connection.channel()

    channel.queue_declare(queue='Repositories')

    for repo in repositories:
        message = {
            'client_id': client_id,
            'repo_url': repo
        }
        channel.basic_publish(exchange='', routing_key='Repositories', body=json.dumps(message))
        print(f" [x] Sent '{message}'")

    connection.close()
    return {"status": "success", "message": "Repositories inviate con successo a RabbitMQ"}

# Function to validate GitHub link
def is_valid_github_link(link):
    github_repo_pattern = r"^https:\/\/github\.com\/([^\/]+)\/([^\/]+)"
    return re.match(github_repo_pattern, link)

# Function to check if a GitHub repository exists
def check_github_repo_exists(link):
    api_url = f"https://api.github.com/repos/{link.replace('https://github.com/', '')}"
    response = requests.get(api_url)
    return response.status_code == 200

@app.route("/")
def index():
    session['id'] = str(uuid.uuid4())
    return render_template("index.html", client_id=session.get('id'))

@app.route("/search", methods=["POST"])
def search():
    data = request.get_json()
    client_id = data.get('client_id')
    if not client_id:
        return jsonify({"status": "error", "message": "Client not authenticated"}), 403

    linguaggio = data.get("linguaggio")
    topic = data.get("topic")
    risultati_per_pagina = int(data.get("risultati_per_pagina", 10))
    pagine = int(data.get("pagine", 1))

    repositories = trova_repositoriesfixed(linguaggio, risultati_per_pagina, pagine, topic)
    if repositories:
        crea_file_repository(repositories)
        response_data = {"status": "success", "repositories": [repo['html_url'] for repo in repositories]}
        socketio.emit('search_result', response_data, room=client_id)  # Send to specific client
        return jsonify(response_data)
    else:
        response_data = {"status": "error", "message": "Errore nella ricerca dei repository"}
        socketio.emit('search_result', response_data, room=client_id)  # Send to specific client
        return jsonify(response_data), 500

@socketio.on('search')
def handle_search(data):
    client_id = session.get('id')  # Use get to avoid KeyError
    if not client_id:
        emit('search_result', {"status": "error", "message": "Client not authenticated"}, room=data.get("client_id"))
        return

    linguaggio = data.get("linguaggio")
    topic = data.get("topic")
    risultati_per_pagina = int(data.get("risultati_per_pagina", 10))
    pagine = int(data.get("pagine", 1))

    repositories = trova_repositoriesfixed(linguaggio, risultati_per_pagina, pagine, topic)
    if repositories:
        crea_file_repository(repositories)
        response_data = {"status": "success", "repositories": [repo['html_url'] for repo in repositories]}
        emit('search_result', response_data, room=client_id)  # Send to specific client
    else:
        response_data = {"status": "error", "message": "Errore nella ricerca dei repository"}
        emit('search_result', response_data, room=client_id)  # Send to specific client

@app.route("/searchlink", methods=["POST"])
def searchlink():
    data = request.json
    link = data.get("link")

    if not link:
        return jsonify({"status": "error", "message": "Nessun link fornito."}), 400

    if not is_valid_github_link(link):
        return jsonify({"status": "error", "message": "Il link fornito non è un link valido di GitHub."}), 400

    if not check_github_repo_exists(link):
        return jsonify({"status": "error", "message": "Il repository non esiste su GitHub."}), 400

    aggiungi_repository(link)
    return jsonify({"status": "success", "message": "Repository aggiunto con successo.", "repository": link})

@app.route("/send", methods=["POST"])
def send():
    data = request.json
    client_id = data.get("client_id")

    if not client_id:
        return jsonify({"status": "error", "message": "Client not authenticated"}), 403

    result = invia_repositories(client_id)
    if result["status"] == "success":
        return jsonify(result)
    else:
        return jsonify(result), 500

@app.route('/analysis_results', methods=['POST'])
def analysis_results():
    event_data = request.json
    event_name = f'new_analysis_result_{event_data.get('client_id')}'
    print(f'Emitting event: {event_name}')
    
    # Emit the event to the specific client
    socketio.emit(event_name, event_data, room=event_data.get('client_id'))
    name = event_data.get('name', 'default_name')  
    directory = "analyzed_repositories"
    os.makedirs(directory, exist_ok=True)
    file_name = os.path.join(directory, f"{name}.json")
    with open(file_name, 'w') as file:
        json.dump(event_data, file, indent=4)
    return jsonify({"status": "success", "results": event_data})

@app.route('/getData/<filename>')
def get_data(filename):
    directory = "analyzed_repositories"
    file_path = os.path.join(directory, f'{filename}.json')
    if not os.path.exists(file_path):
        abort(404)
    with open(file_path, 'r') as json_file:
        data = json.load(json_file)
    return render_template('display.html', data=data)

@socketio.on('connect')
def handle_connect():
    # if 'id' not in session:
    #     session['id'] = str(uuid.uuid4())
    
    # Emit connected event with session ID
    join_room(session.get('id'))
    emit('connected', {'id': session['id']})
    
    # Print session ID for debugging
    print("Client connected with session ID:", session['id'])

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
