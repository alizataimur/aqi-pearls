import gradio as gr
import spaces
import torch

# Check if CUDA is available locally; default to CPU if not
device = "cuda" if torch.cuda.is_available() else "cpu"
zero = torch.Tensor([0]).to(device)
print(f"Global scope device: {zero.device}")


@spaces.GPU
def greet(n):
    # On Hugging Face ZeroGPU, CUDA becomes available inside decorated functions
    current_device = "cuda" if torch.cuda.is_available() else "cpu"
    gpu_zero = torch.Tensor([0]).to(current_device)
    print(f"Function scope device: {gpu_zero.device}")

    return f"Hello {gpu_zero + n} Tensor"


demo = gr.Interface(fn=greet, inputs=gr.Number(), outputs=gr.Text())
demo.launch()