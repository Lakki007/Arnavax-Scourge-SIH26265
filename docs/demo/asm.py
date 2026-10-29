import json,glob,subprocess as sp
V="/tmp/claude-0/vid/";A=V+"asm/"
def g(p):return glob.glob(p)[0]
# (name, src, lead_trim, target, speed)
segs=[("hook",g(V+"cards/raw_hook/*.webm"),0.5,8.6,1),
("stakes",g(V+"cards/raw_stakes/*.webm"),0.5,9.2,1),
("baseline",g(V+"prod/baseline/*.webm"),0.3,9.2,1),
("brand",g(V+"cards/raw_brand/*.webm"),0.5,4.6,1),
("prove",g(V+"prod/prove/*.webm"),0.4,21.0,(24.16-0.4)/21.0),
("evidence",g(V+"prod/evidence/*.webm"),0.2,17.0,1),
("act",g(V+"prod/act/*.webm"),0.3,6.0,1),
("honest",g(V+"cards/raw_honest/*.webm"),0.5,7.0,1),
("close",g(V+"cards/raw_close/*.webm"),0.5,7.4,1)]
for n,s,t,d,sp_ in segs:
  vf=f"setpts=(PTS-STARTPTS)/{sp_},fps=25,scale=1920:1080,tpad=stop_mode=clone:stop_duration=5,trim=duration={d},setpts=PTS-STARTPTS,format=yuv420p"
  sp.run(["ffmpeg","-y","-v","error","-ss",str(t),"-i",s,"-vf",vf,"-an","-c:v","libx264","-preset","veryfast","-crf","18",A+n+".mp4"],check=True)
# concat with 0.4s fades
X=0.4;inp=[];fc=[];off=0;prev="0:v"
for i,(n,*_ ) in enumerate(segs):inp+=["-i",A+n+".mp4"]
for i in range(1,len(segs)):
  off+=segs[i-1][3]-X; out=f"v{i}"
  fc.append(f"[{prev}][{i}:v]xfade=transition=fade:duration={X}:offset={off:.2f}[{out}]");prev=out
sp.run(["ffmpeg","-y","-v","error",*inp,"-filter_complex",";".join(fc),"-map",f"[{prev}]","-c:v","libx264","-preset","veryfast","-crf","18",A+"video.mp4"],check=True)
starts=[]; acc=0
for i,s in enumerate(segs): starts.append(acc); acc+=s[3]-X
total=acc+X
vo=json.load(open(V+"vo.json")); st=dict(zip([s[0] for s in segs],starts))
offs={"hook":0.5,"stakes":st["stakes"]+0.6,"baseline":st["baseline"]+0.6,"brand":st["brand"]+0.8,"prove":st["prove"]+0.6,"evidence":st["evidence"]+0.6,"act":st["act"]+0.3,"close":st["close"]+0.9}
# audio mix
ai=[];af=[];lab=[]
for k,v in enumerate(vo):
  ai+=["-i",V+f"vo_{v['name']}.wav"];ms=int(offs[v['name']]*1000)
  af.append(f"[{k}:a]aresample=48000,adelay={ms}|{ms}[a{k}]");lab.append(f"[a{k}]")
af.append("".join(lab)+f"amix=inputs={len(vo)}:normalize=0,apad,atrim=0:{total:.2f},loudnorm=I=-16:TP=-1.5[aout]")
sp.run(["ffmpeg","-y","-v","error",*ai,"-filter_complex",";".join(af),"-map","[aout]","-ar","48000",A+"vo.wav"],check=True)
# captions: split lines into sentences, time proportionally
def ts(x):h=int(x//3600);m=int(x%3600//60);s=x%60;return f"{h}:{m:02d}:{s:05.2f}"
ass="""[Script Info]
PlayResX:1920
PlayResY:1080
[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,OutlineColour,BackColour,Bold,Italic,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV
Style: C,Inter,40,&H00FFFFFF,&H00000000,&HA0000000,1,0,3,14,0,2,200,200,70
[Events]
Format: Layer,Start,End,Style,Text
"""
import re
for v in vo:
  parts=[p for p in re.split(r'(?<=[.?!])\s+',v['text']) if p];L=sum(len(p) for p in parts);t=offs[v['name']]
  for p in parts:
    d=v['dur']*len(p)/L;ass+=f"Dialogue: 0,{ts(t)},{ts(t+d)},C,{p}\n";t+=d
open(A+"cap.ass","w").write(ass)
out="/tmp/claude-0/ppt/video/scourge_demo_90s.mp4"
sp.run(["ffmpeg","-y","-v","error","-i",A+"video.mp4","-i",A+"vo.wav","-vf","ass="+A+"cap.ass","-c:v","libx264","-preset","veryfast","-crf","20","-c:a","aac","-b:a","160k","-shortest","-movflags","+faststart",out],check=True)
print(total,offs)
