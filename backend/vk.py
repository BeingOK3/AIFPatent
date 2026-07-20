import httpx, json, time, sys
body = {"text":"只回答：收到","model":"agent-plan/glm-5.2","files":[],"session_id":None}
t0=time.time(); nout=0
with httpx.stream("POST","http://localhost:8001/api/run",json=body,timeout=120) as r:
    buf=""
    for chunk in r.iter_text():
        buf+=chunk
        while "\n\n" in buf:
            block,buf=buf.split("\n\n",1)
            line=[l for l in block.split("\n") if l.startswith("data: ")]
            if not line: continue
            evt=json.loads(line[0][6:])
            t=time.time()-t0
            if evt["type"]=="output": nout+=1; print(f"[{t:.1f}s] OUT: {evt['text'][:50]}",flush=True)
            elif evt["type"]=="done": print(f"\nDONE @ {t:.1f}s output={nout}",flush=True); sys.exit(0)
            elif evt["type"]=="error": print(f"ERROR: {evt['error'][:200]}",flush=True); sys.exit(1)