from sense_hat import SenseHat
import subprocess
import threading
import time
import sounddevice as sd

sense = SenseHat()
flag = False

def run_script(path):
    return subprocess.Popen(["python3", path])

def joystick():
     pressed_time = None
     while True:
        events = sense.stick.get_events()
        for e in events:
         if e.action == "pressed":
             pressed_time = time.time()
         if e.action == "released":
                try:
                    duration = float(time.time()) - float(pressed_time)
                    if(duration > 1):
                        return
                    pressed_time = None
                except Exception:
                    pass
                finally:
                    pass
        time.sleep(0.1)
        """
                    if not flag:
                      flag = True
                      run_script("Pi5 CPU Temp + Fans Control/sensestart.py",2)
                    else:
                      flag = False
                      run_script("Pi5 CPU Temp + Fans Control/provider2.py",2)
                    """ 
                 

if __name__ == '__main__':

    current = run_script("Pi5 CPU Temp + Fans Control/sensestart.py")

    while True:
       
       joystick()

       current.terminate()
       current.wait()

       if current.args[1] == "Pi5 CPU Temp + Fans Control/sensestart.py":
           current = run_script("Pi5 CPU Temp + Fans Control/provider2.py")
       else:
           current = run_script("Pi5 CPU Temp + Fans Control/sensestart.py")