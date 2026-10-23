"""Shared unchanged V0.1 guide expectations from the original host tests."""

from jumperfree.routing.steiner import crossbar_create_net, crossbar_init_state
from jumperfree.routing.v01_topology import v01_topology


def example_state() -> dict:
    state = crossbar_init_state(v01_topology())
    state = crossbar_create_net(state, "zeta", ["Clip0", "Clip15"])
    return crossbar_create_net(state, "alpha", ["Clip2", "Clip10", "Clip8"])


EXAMPLE_PLAN = [
    (0, 0, 255, 0), (2, 255, 0, 0), (8, 255, 0, 0),
    (10, 255, 0, 0), (15, 0, 255, 0),
]
EXAMPLE_FRAMES = [
    b"JF1 CLRALL\n", b"JF1 SET 0 0 255 0\n", b"JF1 SET 2 255 0 0\n",
    b"JF1 SET 8 255 0 0\n", b"JF1 SET 10 255 0 0\n", b"JF1 SET 15 0 255 0\n",
]
