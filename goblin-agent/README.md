# goblin-agent

Agente de resúmenes con Ollama local.

Plan:
- Lee `transcript.json` del transcriptor (o notas existentes del backend)
- Resumen map-reduce por tramos -> resumen final estructurado:
  decisiones, action items, temas tratados, citas clave
- Modelo objetivo: qwen2.5:7b / llama3.1:8b en la RTX 3050 Ti
