import subprocess
import os

def start_ollama_process():
    #print("Attempting to start Ollama server...")
    try:
        cmd = ["ollama", "serve"] 

        if os.name == 'nt':  
            process = subprocess.Popen(cmd,
                                    creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
                                    stdout=subprocess.DEVNULL, 
                                    stderr=subprocess.DEVNULL) 
        else: 
            process = subprocess.Popen(cmd,
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL,
                                    preexec_fn=os.setsid) 
        return process
    except FileNotFoundError:
        #print("Error: 'ollama' command not found. Please ensure Ollama is installed and in your system's PATH.")
        return None
    except Exception as e:
        #print(f"Error starting Ollama server: {e}")
        return None