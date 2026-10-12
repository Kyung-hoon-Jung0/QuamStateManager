import numpy as np
import pytest
from quam_state_manager.core.mapfit.contour import centers, levels, contours, simplify


def test_level_rules_and_formula():
    z = np.arange(100.).reshape(10,10)
    assert levels(z, "erroramp") == []
    assert levels(z, "landscape", {"circular": 360, "target_deg": 160}) == [155,160,165]
    assert levels(z, "landscape", {"circular": 1}) == pytest.approx([175/360,.5,185/360])
    assert levels(z, "landscape", {"l1_amp_max": .2}) == [.2]
    assert levels(z, "landscape") == [np.percentile(z,98)/2]
    assert levels(z, "ridge") == [49.5]
    assert levels(z, "unknown", mode="custom", level=22) == [22]
    with pytest.raises(ValueError):
        levels(z, "unknown", mode="custom")


def test_edges_contour_and_interpolation_formula():
    edges = np.arange(12.)
    x = centers(edges, 11)
    z = np.tile(x, (11,1))
    result = contours(z, edges, edges, [5])[0]
    assert np.allclose(result["full_lines"][0][:,0],5)
    assert np.array_equal(x, np.arange(11)+.5)
    assert len(result["lines"][0]) == 2
    with pytest.raises(ValueError):
        centers([0,2,1],3)


def test_simplification_distance_budget_and_full_geometry():
    x = np.arange(5000.)
    line = np.column_stack((x, .05*np.sin(x)))
    assert len(simplify(line)) == 2
    z = np.tile(np.arange(300.), (300,1))+.5*np.sin(np.arange(300.))[:,None]
    result = contours(z,np.arange(300.),np.arange(300.),[150],max_vertices=12)[0]
    assert sum(map(len,result["lines"])) <= 12
    assert len(result["full_lines"][0]) > 200


def test_circle_seam_and_nan_hole():
    x = np.linspace(-20,20,81)
    z = np.tile(x%360,(31,1))
    result = contours(z,x,np.arange(31.),[0,180],circular=360)
    assert np.allclose(result[0]["full_lines"][0][:,0],0)
    assert result[1]["full_lines"] == []
    z[10:20,39:42] = np.nan
    assert len(contours(z,x,np.arange(31.),[0],circular=360)[0]["full_lines"]) == 2
