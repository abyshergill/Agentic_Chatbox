import requests
from ollama import Client 

def run_llm_inference(prompt, model, temperature, response_queue):
    try:
        client = Client(host='http://localhost:11434')
        for chunk in client.generate(
            model=model,
            prompt=prompt,
            options={'temperature': temperature},
            stream=True 
        ):
            if 'response' in chunk:
                token = chunk['response']
                response_queue.put(token) 
            if chunk.get('done'):
                break 

    except requests.exceptions.ConnectionError:
        response_queue.put("Error: Could not connect to Ollama server. Is it running?")
    except Exception as e:
        response_queue.put(f"Error querying LLM: {e}")
    finally:
        response_queue.put("[END_OF_STREAM]")