import numpy as np
import sounddevice as sd

d = 1
f = 440
r = 44100

t = np.linspace(0,d,int(r*d), endpoint=False)

w = 0.5 * np.sin(2*np.pi*f*t)

sd.play(w,r)
sd.wait()