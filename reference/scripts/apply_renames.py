# Apply a rename plan TSV (target, parent, from, to). Two-step rename; no overwrite; logs to <plan>.log
import os,sys,time
HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.environ.get('ASTRO_ROOT') or os.path.dirname(os.path.dirname(HERE))
plan=sys.argv[1]; log=plan+'.log'; done=set()
if os.path.exists(log): done={l.split('\t',1)[1].rstrip('\n') for l in open(log) if l.startswith('OK\t') or l.startswith('SKIP\t')}
t0=time.time(); rows=[l.rstrip('\n').split('\t') for l in open(plan).read().splitlines()[1:] if l.strip()]
with open(log,'a') as L:
    for t,par,fr,to in rows:
        key=f"{t}/{par}/{fr}"
        if key in done: continue
        if time.time()-t0>140: print('PAUSE'); sys.exit()
        base=os.path.join(ROOT,t,par); src=os.path.join(base,fr); dst=os.path.join(base,to); tmp=os.path.join(base,'__tmp_ren__')
        if not os.path.isdir(src) or os.path.exists(tmp): L.write(f"SKIP\t{key}\tmissing-or-tmp\n"); continue
        if os.path.exists(dst) and os.path.realpath(dst)!=os.path.realpath(src) and fr.lower()!=to.lower(): L.write(f"SKIP\t{key}\texists\n"); continue
        n=len(os.listdir(src)); os.rename(src,tmp); os.rename(tmp,dst); m=len(os.listdir(dst))
        L.write(f"OK\t{key}\t-> {to}\t{n}->{m}\n")
print('ALLDONE')
