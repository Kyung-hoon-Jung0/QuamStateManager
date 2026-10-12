"""Name-free deterministic map generators shared by pins and bias sweeps."""
import numpy as np


def sample(model, shift=0., noise=0., seed=7, asymmetry=0., k=1.):
    rng = np.random.default_rng(seed)
    meta = {}
    if model == "erroramp":
        x,y = np.linspace(.8,1.2,81),np.arange(1,32,2.)
        truth = .983+shift*.005
        z = .5+.5*np.exp(-y[:,None]/40)*np.cos(y[:,None]*np.pi*(1+k*(x[None,:]/truth-1)))
        meta = {"multipulse_slope_ratio":k}
        values = {"a_pi":truth}
    elif model == "ridge":
        x,y = np.arange(61.),np.arange(201.)
        truth = 30.37+shift
        center = 140-.07*(x-truth)**2
        eps = (y[:,None]-center)/4
        z = 1/(1+eps**2)+asymmetry*eps/(1+eps**2)
        meta = {"hwhm_pts":4}
        values = {"x":truth,"y":140.}
    elif model == "chevron":
        x,y = np.linspace(0,4,201),np.linspace(-2,2,41)
        truth = .137+shift*.1
        f = np.sqrt(1.7**2+(1.3*(y-truth))**2)
        z = .5+.45*np.exp(-x[None,:]/8)*np.cos(2*np.pi*f[:,None]*x)
        meta = {"time_units":"s"}
        values = {"dv0":truth,"two_j_hz":1.7,"t_pi":1/3.4}
    else:
        x,y = np.arange(81.),np.arange(71.)
        truth = 40.3+shift
        xx,yy = np.meshgrid(x,y)
        z = ((xx-truth)/20)**2+((yy-33.7)/20)**2+asymmetry*((xx-truth)/20)**3
        values = {"x":truth,"y":33.7}
        if model == "landscape":
            meta = {"phase":180+(yy-33.7)*3,"circular":360.,"pick_method":"contour_fit"}
    z = z+rng.normal(0,noise,z.shape)
    return z,x,y,meta,values
