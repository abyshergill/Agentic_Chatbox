# BOT Octopus | Local LLM Chatbox
## Introduction
This desktop application, built with customtkinter, provides an interactive chat interface for local Large Language Models (LLMs) served via Ollama. It's designed to be responsive, allowing you to interact with your LLM without the GUI freezing, and it includes features for customizing the LLM's behavior and stopping ongoing generations.

## Update
24 July'25 
- fix the window hang issue 


## Features
* **Customizable LLM Interaction:** Easily chat with your local LLMs.

* **Model Selection:** Automatically detects and lists models available from your running Ollama server.

* **Temperature Control:** Adjust the creativity (Temperature) of the LLM's responses using a slider (Creativity from 0.0 to 1.0).

* **Multiline Input:** Type longer queries using a multiline input box; press 
    * Enter to send
    * Shift + Enter for a new line.

* **Responsive GUI:** Utilizes multiprocessing to offload LLM inference to a separate process, ensuring the main application remains fully responsive.

* **Streaming Output:** LLM responses appear token by token (word by word) directly in the main chat history, just like a command-line interface.

* **Markdown Rendering:** The final LLM response is rendered with basic `Markdown formatting (headers, bold, italic, list items)` for improved readability.

* **Stop Generation Button:** Interrupts an ongoing LLM response at any time, providing control over long generations.

* **Thinking Status:** `A "LLM (BOT Octobpus): Thinking..."` message appears in the main window while the LLM is generating a response, clearing once the response is complete or stopped.

* **Assistant Persona:** The application welcomes you as `"I am BOT Octopus!: Here to assist you with anything you need!"`

## Prerequisites
Before running the application, you need to have:

* **Python 3.7+:** It's highly recommended to use Python 3.11 or 3.12 for best compatibility with customtkinter. Because GUI is written using customtkinter.

* **Install Ollama**
   - Visit [Ollama's website](https://ollama.ai) to download and install
   - Pull required models:
     ```bash
     ollama pull llama3.2  # or your preferred model
     ```

* **Local LLM Models:** Pull at least one LLM model using Ollama (e.g., ollama pull llama3).

## Installation
* **Clone or Download the Code:** Save the Python code (provided in the Canvas) to a file named llm_chat_app.py.

* **Install Dependencies:** Open your terminal or command prompt and run the following command:
    ```bash
    pip install -r requirements.txt
    ```

## How to Run
* **Ensure Ollama is Running:** Make sure your Ollama server is active and accessible (usually on http://localhost:11434).


* **Run the Python Application:** Navigate to the directory where you saved llm_chat_app.py in your terminal and execute:
    ```bash
    python app.py
    ```

## Usage
* **Select a Model:** Use the "Choose Model" dropdown to select an available LLM. If no models are found, ensure Ollama is running and you have pulled models.

* **Set Creatitivity ( Temperature ):** Adjust the "Creatitivity" slider to control the LLM's response creativity (0.0 for deterministic, 1.0 for highly creative).

* **Type Your Message:** Enter your query in the multiline text box.

    * Press Enter to send your message.

    * Press Shift + Enter to insert a new line in your input.

* **Observe Response:** The LLM's response will stream word by word into the chat history. The status label will show "LLM: Thinking..." while it generates.

* **Stop Generation:** If the LLM is taking too long or producing an undesirable response, click the "Stop" button (red) to terminate the generation.

## Code Structure Overview
* **get_local_llm_models():** Fetches a list of installed LLM models from the local Ollama API.

* **run_llm_inference():** This function runs in a separate process. It communicates with the Ollama client to generate responses in a streaming fashion, sending each token back to the main application via a multiprocessing.Queue. This is crucial for preventing the GUI from freezing.

* **LLMChatApp Class:**

    * **__init__():** Sets up the main window, all UI elements (chat history, input, buttons, sliders, dropdowns), and initializes multiprocessing queues and variables.

    * **send_message():** Handles user input, clears the input box, displays the user's message, sets the "Thinking" status, enables the "Stop" button, and starts the run_llm_inference function in a new process.

    * **poll_llm_response_queue():** This method continuously checks the queue for new tokens from the LLM process. It displays each token as it arrives, accumulating the full response. Once [END_OF_STREAM] is received, it clears the "Thinking" status, disables the "Stop" button, and applies Markdown formatting to the complete LLM response.

    * **stop_llm_generation():** Terminates the active LLM process, clears the queue, and updates the UI accordingly.

    * **_insert_markdown_text():** A utility function to parse simplified Markdown and apply tkinter tags for formatting text within the ScrolledText widget.

    * **handle_enter_key():** Manages Enter and Shift + Enter key presses for multiline input.

    * **display_message():** A general utility to insert messages (user or system) into the chat history.

    * **if __name__ == "__main__":** block: Initializes multiprocessing.freeze_support() (important for Windows executables) and starts the main application loop.

    ## Project Structure (Expected)
```
Local LLM Chatbox/
├── app.py                 (Application for GUI)
├── icon/
│   └── icon.ico           (Optional: Application icon)
├── utility/               (Deal with Ollama related opeation)
│   ├── getmodels.py       (This will fetch all the availabe model)
│   └── runllm.py          (Run the selected model)
│   └── startollama.py     (Start the ollama to work with)
├── readme.md              (Project Information)
├── license.txt

```

## License

This project is licensed under the Apache 2.0 License.

---
`Note :`- 
* Who want to directly use on windows with .exe file for try can contact me.
* With .exe you can run this application without python or any other application if you already have model in your system.


Creator : Aby Email : shergillkuldeep@outlook.com | Repo : [github.com/abyshergill](https://github.com/abyshergill/Local_LLM_Chatbox)