# Paste into a NEW cell in the running Kaggle notebook and run it ONCE.
# Adds a /generate endpoint that accepts its own system prompt (used for extraction and translation).
class GenIn(BaseModel):
    system: str
    messages: list
    max_new_tokens: int = 300
    temperature: float = 0.2

def generate_raw(system, messages, max_new_tokens=300, temperature=0.2):
    text = tok.apply_chat_template([{"role": "system", "content": system}] + messages,
                                   tokenize=False, add_generation_prompt=True)
    inputs = tok(text, return_tensors="pt", add_special_tokens=False).to(llm.device)
    kw = dict(max_new_tokens=max_new_tokens)
    if temperature and temperature > 0:
        kw.update(do_sample=True, temperature=temperature, top_p=0.9)
    else:
        kw.update(do_sample=False)
    with lock, torch.no_grad():
        out = llm.generate(**inputs, **kw)
    return tok.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True).strip()

@app.post("/generate")
def generate_endpoint(body: GenIn, x_api_key: str = Header(None)):
    check(x_api_key)
    return {"reply": generate_raw(body.system, body.messages, body.max_new_tokens, body.temperature)}

print("added /generate")
