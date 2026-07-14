import os
s = open('/app/main.py').read()
block = '''    "graph_store": {
        "provider": "neo4j",
        "config": {"url": NEO4J_URI, "username": NEO4J_USERNAME, "password": NEO4J_PASSWORD},
    },
'''
s = s.replace(block, '')
s = s.replace('"model": "gpt-4o"', '"model": os.environ.get("MEM0_LLM_MODEL", "gpt-4o")')
s = s.replace('"model": "text-embedding-3-small"',
              '"model": os.environ.get("MEM0_EMBED_MODEL", "text-embedding-3-small")')
open('/app/main.py', 'w').write(s)
print('main.py parcheado: sin graph_store, modelos por env')
