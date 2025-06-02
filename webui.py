import socket
import json
from flask import Flask, render_template
from flask_sock import Sock
import threading

app = Flask(__name__)

fsock = Sock(app)

# global varaibles
# ****************
sock = None
llm_work_id = None

wsDict = {}
REQUEST_ID_FORMAT = "WEB_{}"
id = 1

# Status message for the web UI
STATUS_COMPLETION = "{ \"status\": \"Answer complete.\"}"
STATUS_SUBMITTED = "{ \"status\": \"Question submitted.\"}"

# ****************

# create tcp connection to LLM
def create_tcp_connection(host, port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((host, port))
    return sock

# convert object to json and send over socket
def send_json(sock, data):
    # print("send_json.")
    json_data = json.dumps(data, ensure_ascii=False) + '\n'
    sock.sendall(json_data.encode('utf-8'))

# accumulate and return one line of incoming response
def receive_response(sock):
    # print("Waiting for response.")
    response = ''
    while True:
        part = sock.recv(4096).decode('utf-8')
        response += part
        if '\n' in response:
            break
    return response.strip()


# close socket connection
def close_connection(sock):
    print("close_connection reached.")
    if sock:
        sock.close()

# return a request object to initialise a LLM session
def create_init_data():
    return {
        "request_id": "llm_001",
        "work_id": "llm",
        "action": "setup",
        "object": "llm.setup",
        "data": {
            "model": "qwen2.5-0.5B-prefill-20e",
            "response_format": "llm.utf-8.stream",
            "input": "llm.utf-8.stream",
            "enoutput": True,
            "max_token_len": 1023,
            "prompt": "You are a knowledgeable assistant capable of answering various questions and providing information."
        }
    }

# verfy setup response
def parse_setup_response(response_data, sent_request_id):
    error = response_data.get('error')
    request_id = response_data.get('request_id')

    if request_id != sent_request_id:
        print(f"Request ID mismatch: sent {sent_request_id}, received {request_id}")
        return None

    if error and error.get('code') != 0:
        print(f"Error Code: {error['code']}, Message: {error['message']}")
        return None

    return response_data.get('work_id')

# set up a LLM session
def setup(sock, init_data):
    sent_request_id = init_data['request_id']
    send_json(sock, init_data)
    response = receive_response(sock)
    response_data = json.loads(response)
    return parse_setup_response(response_data, sent_request_id)

# verify an inference response
def parse_inference_response(response_data):
    error = response_data.get('error')
    if error and error.get('code') != 0:
        print(f"Error Code: {error['code']}, Message: {error['message']}")
        return None
    
    request_id = response_data.get('request_id')

    return request_id, response_data.get('data')

# render home page
@app.route('/')
def renderPage():
    print("renderPage reached.")
    return render_template('index.html')


# let client register its websocket for communication
@fsock.route('/ws')
def events(ws):
    global id, sock, llm_work_id, wsDict
    print("Websocket connected.")

    # invoke inference with the question received from the webui
    while True:
        msg = json.loads(ws.receive())
        if "question" in msg:
            print("Recevied client question: " + msg["question"])
            requestId = REQUEST_ID_FORMAT.format(id)
            id += 1
            wsDict[requestId] = ws

            send_json(sock, {
                "request_id": requestId,
                "work_id": llm_work_id,
                "action": "inference",
                "object": "llm.utf-8.stream",
                "data": {
                    "delta": msg["question"],
                    "index": 0,
                    "finish": True
                }
            })

            ws.send(STATUS_SUBMITTED)


# thread to create a LLM session,wait for inference responses and
# send partial responses over a websocket to a web client
def llm():
    global sock, llm_work_id, wsDict

    # connect to LLM host:port
    sock = create_tcp_connection("localhost", 10001)

    try:
        print("Initializing LLM...")
        init_data = create_init_data()
        llm_work_id = setup(sock, init_data)
        print("LLM initialisation completed.")

        while True:
            # print("Waiting for inference response.")
            response = receive_response(sock)
            # print("Inference response: " + response)

            response_data = json.loads(response)

            # print("Handling inference response.")
            request_id, data = parse_inference_response(response_data)
            if data is None:
                print("Data is None.")
                continue

            finish = data.get('finish')
            ws = wsDict.get(request_id)
            if ws is None:
                print("ws in None.")
                continue

            if finish:
                ws.send(STATUS_COMPLETION)
                wsDict.pop(request_id, None)
                continue

            # print("Sending inference response to web page.")
            delta = data.get('delta')
            msg = {}
            msg["chunk"] = delta
            msg["status"] = "Receiving answer."
            ws.send(json.dumps(msg))


            # print(delta)

    finally:
        close_connection(sock)


if __name__ == "__main__":
    # create a thread to handle LLM sessions
    thread = threading.Thread(target = llm)
    thread.start()

    app.run(host='0.0.0.0', port=8080)
