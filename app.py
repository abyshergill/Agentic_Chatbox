import customtkinter as ctk
import tkinter as tk
from tkinter import scrolledtext
import random
import time
import multiprocessing
import re
import os

from utility.getmodels import get_local_llm_models
from utility.runllm import run_llm_inference
from utility.startollama import start_ollama_process


class LLMChatApp(ctk.CTk):
    def __init__(self):
        
        ctk.set_appearance_mode("Light")      # System, Dark, Light
        ctk.set_default_color_theme("blue")   # blue, dark-blue, green

        super().__init__()
        self.title("Octopus | Local LLM Chatbox")
        self.geometry("900x700")
        self.minsize(700, 600) 

        self.stop_generation = False

        icon_path = os.path.join(os.path.dirname(__file__), "icon", "icon.ico")
        if os.path.exists(icon_path):
             self.iconbitmap(icon_path)

        # Main Window
        self.rowconfigure(0, weight=1)  
        self.rowconfigure(1, weight=0)  
        self.rowconfigure(2, weight=0)  
        self.rowconfigure(3, weight=0)  
        self.columnconfigure(0, weight=1) 
        self.columnconfigure(1, weight=0) 

        # History
        self.chat_frame = ctk.CTkFrame(self)
        self.chat_frame.grid(row=0, column=0, columnspan=2, padx=10, pady=10, sticky="nsew")
        self.chat_frame.rowconfigure(0, weight=1)
        self.chat_frame.columnconfigure(0, weight=1)

        # Chat history 
        self.chat_history = scrolledtext.ScrolledText(
            self.chat_frame,
            wrap=tk.WORD,
            font=("Helvetica", 14), 
            bg=self.chat_frame._apply_appearance_mode(ctk.ThemeManager.theme["CTkFrame"]["fg_color"]),
            fg=self.chat_frame._apply_appearance_mode(ctk.ThemeManager.theme["CTkLabel"]["text_color"]),
            bd=0, 
            state='disabled' 
        )
        self.chat_history.grid(row=0, column=0, padx=5, pady=5, sticky="nsew")

        # Markdown formatting Config
        self.chat_history.tag_config("blue", foreground="blue")
        self.chat_history.tag_config("green", foreground="green")
        self.chat_history.tag_config("orange", foreground="orange")
        self.chat_history.tag_config("black", foreground="black") 
        self.chat_history.tag_config("bold", font=("Helvetica", 12, "bold"), foreground="black") 
        self.chat_history.tag_config("italic", font=("Helvetica", 12, "italic"), foreground="black")
        self.chat_history.tag_config("h1", font=("Helvetica", 20, "bold"), foreground="black") 
        self.chat_history.tag_config("h2", font=("Helvetica", 18, "bold"), foreground="black") 
        self.chat_history.tag_config("h3", font=("Helvetica", 16, "bold"), foreground="black") 
        self.chat_history.tag_config("list_item", lmargin1=20, lmargin2=40, foreground="black") 

#------> This part show thinking label in the main window
        self.status_label = ctk.CTkLabel(
            self,
            text="", 
            font=("Helvetica", 14, "italic"),
            text_color="gray",
            wraplength=800
        )
        self.status_label.grid(row=1, column=0, columnspan=2, padx=10, pady=(0, 5), sticky="w")

#------> This part will take | User Input | Send | Stop
        self.input_frame = ctk.CTkFrame(self)
        self.input_frame.grid(row=2, column=0, padx=10, pady=(0, 10), sticky="ew", columnspan=2)
        self.input_frame.columnconfigure(0, weight=1) 
        self.input_frame.columnconfigure(1, weight=0) 
        self.input_frame.columnconfigure(2, weight=0) 

#------> User input
        self.user_input = ctk.CTkTextbox(
            self.input_frame,
            wrap="word", 
            height=60, 
            font=("Helvetica", 14)
        )
        self.user_input.insert("0.0", "Type your message...") 
        self.user_input.grid(row=0, column=0, padx=(10, 5), pady=10, sticky="ew")
        self.user_input.bind("<Return>", self.handle_enter_key) #This if i want to bind enter key

        self.send_button = ctk.CTkButton(
            self.input_frame,
            text="Send",
            command=self.send_message,
            width=100,
            height=60, 
            font=("Helvetica", 20, "bold")
        )
        self.send_button.grid(row=0, column=1, padx=(5, 5), pady=10, sticky="e") 

        self.stop_button = ctk.CTkButton(
            self.input_frame,
            text="Stop",
            command=self.stop_llm_generation,
            width=100,
            height=60, 
            font=("Helvetica", 20, "bold"),
            fg_color="red", 
            hover_color="darkred",
            state="disabled" 
        )
        self.stop_button.grid(row=0, column=2, padx=(5, 10), pady=10, sticky="e") 

#------> Model Selection and Set the Temperature
        self.control_panel = ctk.CTkFrame(self)
        self.control_panel.grid(row=3, column=0, padx=10, pady=(10, 10), sticky="ew", columnspan=2)
        self.control_panel.columnconfigure((0, 1, 2), weight=1) 

#-------> Multiprocessing variables
        self.response_queue = multiprocessing.Queue()
        self.llm_process = None
        self.current_llm_full_response = "" 
        self.llm_response_content_start_idx = None 
        self.ollama_server_process = None 

#-------> Start Ollama 
        ollama_running, detected_models = get_local_llm_models() 
        if not ollama_running:
            #print("Ollama server not detected. Attempting to start...")
            self.display_message("System", "Attempting to start Ollama server...", "blue")
            self.ollama_server_process = start_ollama_process()
            if self.ollama_server_process:
                startup_attempts = 0
                max_attempts = 50
                while startup_attempts < max_attempts:
                    time.sleep(0.1) 
                    ollama_running, detected_models = get_local_llm_models() 
                    if ollama_running:
                        self.display_message("System", "Ollama server started successfully. Fetching models...", "blue")
                        break
                    startup_attempts += 1
                if not ollama_running:
                    error_msg = "Failed to start Ollama server or connect to it. Please start Ollama manually."
                    #print(error_msg)
                    self.display_message("System", error_msg, "red")
            else:
                error_msg = "Could not initiate Ollama server startup process. Check your Ollama installation and PATH."
                #print(error_msg)
                self.display_message("System", error_msg, "red")

        self.model_options = detected_models if detected_models else ["No local models found (Start Ollama manually)"]
        
        # Model Selection
        self.model_label = ctk.CTkLabel(self.control_panel, text="Choose Model:", font=("Helvetica", 12))
        self.model_label.grid(row=0, column=0, padx=10, pady=5, sticky="w")
        
        self.selected_model = ctk.StringVar(value=self.model_options[0]) 
        self.model_dropdown = ctk.CTkOptionMenu(
            self.control_panel,
            values=self.model_options,
            variable=self.selected_model,
            width=180,
            font=("Helvetica", 15)
        )
        self.model_dropdown.grid(row=0, column=1, padx=10, pady=5, sticky="ew")

        # Temperature Slider
        self.temperature_label = ctk.CTkLabel(self.control_panel, text="Temperature:", font=("Helvetica", 12))
        self.temperature_label.grid(row=1, column=0, padx=10, pady=5, sticky="w")

        self.temperature_slider = ctk.CTkSlider(
            self.control_panel,
            from_=0.0,
            to=1.0,
            number_of_steps=100, 
            command=self.update_temperature_label
        )
        self.temperature_slider.set(0.7) 
        self.temperature_slider.grid(row=1, column=1, padx=10, pady=5, sticky="ew")

        self.temperature_value_label = ctk.CTkLabel(
            self.control_panel,
            text=f"{self.temperature_slider.get():.2f}",
            font=("Helvetica", 12, "bold")
        )
        self.temperature_value_label.grid(row=1, column=2, padx=10, pady=5, sticky="w")

        # Initial message
        self.display_message("I am BOT Octopus!", "Here to assist you with anything you need!", "blue")

        # Ceanup handler When App Close
        self.protocol("WM_DELETE_WINDOW", self.on_closing)
 
    def update_temperature_label(self, value):
        self.temperature_value_label.configure(text=f"{value:.2f}")

    def handle_enter_key(self, event=None):
        if event.state & 0x1: 
            return
        else:
            self.send_message()
            return "break" 

    def on_closing(self):
        try:
            if self.ollama_server_process and self.ollama_server_process.poll() is None:
                self.ollama_server_process.terminate()
                self.ollama_server_process.wait(timeout=5)
                if self.ollama_server_process.poll() is None:
                    self.ollama_server_process.kill()
        except Exception as e:
            self.display_message(f"Error terminating Ollama subprocess: {e}")

        try:
            if self.llm_process and self.llm_process.is_alive():
                self.llm_process.terminate()
                self.llm_process.join(timeout=3)
                if self.llm_process.is_alive():
                    self.llm_process.kill()
        except Exception as e:
            self.display_message(f"Error terminating LLM subprocess: {e}")

        self.destroy()


    def send_message(self):
        user_text = self.user_input.get("0.0", "end").strip() 
        if not user_text:
            return 

        self.chat_history.configure(state='normal')
        self.chat_history.insert(tk.END, f"You: {user_text}\n\n", "green")
        self.chat_history.configure(state='disabled')
        self.chat_history.see(tk.END)

        self.user_input.delete("0.0", "end") 

        
        self.stop_llm_generation(force_silent=True) 

        model = self.selected_model.get()
        if model == "No local models found (Start Ollama)":
            self.display_message("System", "Please start your Ollama server and pull a model before asking questions.", "red")
            return

        temperature = self.temperature_slider.get()

        self.current_llm_full_response = ""

        # Display "Thinking..." message in the status label
        self.status_label.configure(text="BOT Octopus: Thinking...", text_color="gray")
        self.stop_button.configure(state="normal") 

        # Prepare Chat History
        self.chat_history.configure(state='normal')
        self.chat_history.insert(tk.END, "BOT Octopus: ", "black") 
        self.llm_response_content_start_idx = self.chat_history.index(tk.END)
        self.chat_history.configure(state='disabled')
        self.chat_history.see(tk.END)

        # Start LLM inference in a new process
        self.llm_process = multiprocessing.Process(
            target=run_llm_inference,
            args=(user_text, model, temperature, self.response_queue)
        )
        self.llm_process.start()

        # Start polling the queue 
        self.after(50, self.poll_llm_response_queue) 

    def stop_llm_generation(self, force_silent=False):
        if self.llm_process and self.llm_process.is_alive():
            self.llm_process.terminate()
            self.llm_process.join()
            #print("LLM process terminated by stop button.")  # only for myself Aby

            # Clear any remaining items in the queue 
            while not self.response_queue.empty():
                try:
                    self.response_queue.get_nowait()
                except multiprocessing.queues.Empty:
                    break 

            self.llm_process = None
            self.status_label.configure(text="") #
            self.stop_button.configure(state="disabled") 

            if not force_silent:
                self.chat_history.configure(state='normal')
                self.chat_history.insert(tk.END, "\nGeneration stopped by user.\n\n", "red")
                self.chat_history.see(tk.END)
                self.chat_history.configure(state='disabled')
            
            self.current_llm_full_response = "" 

    def poll_llm_response_queue(self):
        while not self.response_queue.empty():
            token = self.response_queue.get()
            if token == "[END_OF_STREAM]":
                self.status_label.configure(text="") 
                self.stop_button.configure(state="disabled")

                self.chat_history.configure(state='normal')
                current_end_idx = self.chat_history.index(tk.END)
                self.chat_history.delete(self.llm_response_content_start_idx, current_end_idx)
                
                self._insert_markdown_text(self.chat_history, self.current_llm_full_response, "black")
                self.chat_history.insert(tk.END, "\n\n") 
                self.chat_history.see(tk.END)
                self.chat_history.configure(state='disabled')

                self.llm_process.join() 
                self.llm_process = None
                return 

            self.current_llm_full_response += token 

            # Display token immediately 
            self.chat_history.configure(state='normal')
            self.chat_history.insert(tk.END, token, "black") 
            self.chat_history.see(tk.END)
            self.chat_history.configure(state='disabled')

        # Continue polling till EOS
        if self.llm_process and self.llm_process.is_alive():
            self.after(50, self.poll_llm_response_queue)

    def display_message(self, sender, message, color_tag):
        self.chat_history.configure(state='normal') 
        if sender != "LLM":
            self.chat_history.insert(tk.END, f"{sender}: ", color_tag)
            self.chat_history.insert(tk.END, message, color_tag) 
            self.chat_history.insert(tk.END, "\n\n") 
        
        self.chat_history.see(tk.END) 
        self.chat_history.configure(state='disabled') 



#----> This part is not good enough need to improve more
    def _insert_markdown_text(self, text_widget, markdown_text, default_tag):
        lines = markdown_text.split('\n')
        for line in lines:
            line = line.strip() 

            if line.startswith('### '):
                text_widget.insert(tk.END, line[4:].strip() + '\n', ("h3", default_tag))
            elif line.startswith('## '):
                text_widget.insert(tk.END, line[3:].strip() + '\n', ("h2", default_tag))
            elif line.startswith('# '):
                text_widget.insert(tk.END, line[2:].strip() + '\n', ("h1", default_tag))
            elif line.startswith('* ') or line.startswith('- '):
                text_widget.insert(tk.END, "  " + line[2:].strip() + '\n', ("list_item", default_tag))
            else:
                # Handle inline formatting (bold/italic) for regular lines
                # Regex to split on bold (**) and italic (*) markers, keeping the markers
                parts = re.split(r'(\*\*.*?\*\*|\*.*?\*)', line)
                for part in parts:
                    if part.startswith('**') and part.endswith('**'):
                        text_widget.insert(tk.END, part[2:-2], ("bold", default_tag))
                    elif part.startswith('**') and part.endswith('**:'):
                        text_widget.insert(tk.END, part[2:-2], ("bold", default_tag ))                  
                    elif part.startswith('*') and part.endswith('*'):
                        text_widget.insert(tk.END, part[1:-1], ("italic", default_tag))
                    elif part.startswith('*') and part.endswith('*:'):
                        text_widget.insert(tk.END, part[1:-1], ("italic", default_tag))
                    else:
                        text_widget.insert(tk.END, part, default_tag)
                text_widget.insert(tk.END, '\n')

if __name__ == "__main__":
    multiprocessing.freeze_support()
    app = LLMChatApp()
    app.mainloop()

