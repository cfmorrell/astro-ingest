# Apply session rename plan (target, from, to, note). Two-step rename, no overwrite, logs to <plan>.log
import os,sys
HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.path.dirname(os.path.dirname(HERE)); os.chdir(ROOT)
plan=sys.argv[1]; log=open(plan+'.log','a'); ok=sk=0
for l in open(plan).read().splitlines()[1:]:
    t,s,n=l.split('\t')[:3]; a=os.path.join(t,s); b=os.path.join(t,n); tmp=os.path.join(t,'__tmp_ren__')
    if not os.path.isdir(a) or os.path.exists(b) or os.path.exists(tmp): log.write(f"SKIP\t{a}\t{b}\n"); sk+=1; print('SKIP',a,'->',b); continue
    k=len(os.listdir(a)); os.rename(a,tmp); os.rename(tmp,b); m=len(os.listdir(b))
    log.write(f"OK\t{a}\t{b}\t{k}->{m}\n"); ok+=1
    if k!=m: print('COUNT MISMATCH',b)
print('OK',ok,'SKIP',sk)
