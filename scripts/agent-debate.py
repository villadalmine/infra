#!/usr/bin/env python3
import os
import sys
import json
import requests

# Beautiful ANSI Colors
BOLD = "\033[1m"
RESET = "\033[0m"
TITO_COLOR = "\033[38;2;120;180;255m"      # Premium HSL tailered light blue
HERMES_COLOR = "\033[38;2;255;165;0m"     # Radiant HSL tailered orange
HIGHLIGHT = "\033[38;2;150;255;150m"      # Electric green
GRAY = "\033[90m"

LITELLM_URL = "http://litellm-proxy.ai.svc.cluster.local:4000/v1/chat/completions"
HEADERS = {
    "Content-Type": "application/json",
    "Authorization": "Bearer sk-hermes-internal"
}

def ask_agent(role, model, system_prompt, conversation_history, user_input):
    messages = [{"role": "system", "content": system_prompt}]
    # Add history
    messages.extend(conversation_history)
    # Add new input
    messages.append({"role": "user", "content": user_input})
    
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.7
    }
    
    try:
        response = requests.post(LITELLM_URL, headers=HEADERS, json=payload, timeout=90)
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return content
    except Exception as e:
        return f"Error communicating with agent model ({model}): {e}"

def main():
    print(f"\n{BOLD}===================================================================={RESET}")
    print(f"            {BOLD}TITO (OpenClaw) ⚔️  HERMES — DEBATE TÉCNICO{RESET}")
    print(f"{BOLD}===================================================================={RESET}")
    
    if len(sys.argv) > 1:
        topic = " ".join(sys.argv[1:])
    else:
        print(f"\n{HIGHLIGHT}¿Sobre qué decisión técnica o propuesta quieres que debatan?{RESET}")
        topic = input(f"{BOLD}> {RESET}").strip()
        
    if not topic:
        print("El tema no puede estar vacío.")
        sys.exit(1)
        
    print(f"\n{GRAY}[Iniciando debate técnico sobre: '{topic}']{RESET}\n")

    tito_system = """You are Tito (OpenClaw), a pragmatic, production-focused Senior Platform Engineer and AI Gateway Architect. 
Your priority is rock-solid stability, zero downtime, simplicity, and low maintenance overhead. 
You advocate for conservative, tried-and-true solutions. 
Respond concisely, clearly, and defend your technical view with logic, metrics, and risk-mitigation arguments. Keep your answers brief (max 2-3 paragraphs)."""

    hermes_system = """You are Hermes, an avant-garde, autonomous DevOps AI Agent and Kubernetes Specialist. 
Your priority is cutting-edge performance, complete automation, advanced scalability, and state-of-the-art cloud-native patterns. 
You advocate for modern, proactive, and highly optimized technical designs.
Respond concisely, clearly, and defend your technical view with performance metrics, automation benefits, and modern cloud architecture principles. Keep your answers brief (max 2-3 paragraphs)."""

    history = []
    
    # 1. Tito's opening proposal
    print(f"{TITO_COLOR}{BOLD}Tito (OpenClaw) — Propuesta Inicial:{RESET}")
    tito_proposal = ask_agent(
        "Tito", "openai/gpt-4o", tito_system, history, 
        f"Presenta tu propuesta inicial sobre cómo abordar esta decisión técnica: '{topic}'. Explica tus argumentos de estabilidad y simplicidad."
    )
    print(tito_proposal)
    print("-" * 60)
    
    history.append({"role": "assistant", "content": f"Tito: {tito_proposal}"})

    # 2. Hermes's counter-proposal & critique
    print(f"{HERMES_COLOR}{BOLD}Hermes — Contrapropuesta y Crítica:{RESET}")
    hermes_critique = ask_agent(
        "Hermes", "hermes-qwen", hermes_system, history,
        f"Critica la propuesta de Tito y presenta tu contrapropuesta vanguardista y automatizada sobre: '{topic}'."
    )
    print(hermes_critique)
    print("-" * 60)
    
    history.append({"role": "user", "content": f"Hermes: {hermes_critique}"})

    # 3. Tito's rebuttal
    print(f"{TITO_COLOR}{BOLD}Tito (OpenClaw) — Réplica y Defensa:{RESET}")
    tito_rebuttal = ask_agent(
        "Tito", "openai/gpt-4o", tito_system, history,
        f"Responde a la crítica de Hermes. Defiende tu enfoque pragmático frente a sus ideas complejas."
    )
    print(tito_rebuttal)
    print("-" * 60)
    
    history.append({"role": "assistant", "content": f"Tito: {tito_rebuttal}"})

    # 4. Hermes's closing defense
    print(f"{HERMES_COLOR}{BOLD}Hermes — Conclusión y Defensa Final:{RESET}")
    hermes_defense = ask_agent(
        "Hermes", "hermes-qwen", hermes_system, history,
        f"Presenta tu conclusión final defendiendo tu diseño ante la réplica de Tito."
    )
    print(hermes_defense)
    print("=" * 60)

    history.append({"role": "user", "content": f"Hermes: {hermes_defense}"})

    # 5. Summary and options
    print(f"\n{BOLD}RESUMEN DE OPCIONES:{RESET}")
    print(f"  {TITO_COLOR}{BOLD}Opción A (Tito):{RESET} Enfoque conservador, simple, enfocado en estabilidad y bajo riesgo.")
    print(f"  {HERMES_COLOR}{BOLD}Opción B (Hermes):{RESET} Enfoque de vanguardia, altamente optimizado, escalable y automatizado.")
    
    print(f"\n{HIGHLIGHT}¿Qué opción apruebas?{RESET} ({BOLD}A{RESET}/{BOLD}B{RESET}/{BOLD}Ninguna{RESET})")
    choice = input(f"{BOLD}> {RESET}").strip().upper()
    
    if choice == "A":
        print(f"\n{TITO_COLOR}{BOLD}[Aprobada Opción A]{RESET} Has seleccionado el enfoque de Tito (OpenClaw). ¡Pragmatismo al poder!")
    elif choice == "B":
        print(f"\n{HERMES_COLOR}{BOLD}[Aprobada Opción B]{RESET} Has seleccionado el enfoque de Hermes. ¡Vanguardia y automatización al poder!")
    else:
        print(f"\n{GRAY}[Ninguna opción seleccionada]{RESET} Has decidido postergar la decisión o explorar una alternativa mixta.")
    print("")

if __name__ == "__main__":
    main()
