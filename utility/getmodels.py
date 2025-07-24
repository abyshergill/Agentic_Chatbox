import requests
import json

def get_local_llm_models():
    try:
        response = requests.get("http://localhost:11434/api/tags", timeout=1)
        response.raise_for_status()
        data = response.json()
        models = [model["name"] for model in data.get("models", [])]
        return True, models 
        return models
    except requests.RequestException:
        return False, []