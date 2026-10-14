# Changelog

## 1.0.0 — first packaged release

First standalone release. The development tree is not included; this package contains only what a user
needs to install, run and verify the skill.

**Verified capabilities**

* 4-bit ripple-carry adder — 512/512, with a negative control.
* 4-bit subtractor — 512/512, plus three controls that all fail as required.
* 4-bit ALU (Adder + Multiplexer) — 1024/1024.
* **8-bit ALU, tunnel-wired — 1,048,576/1,048,576.**
* 2-bit magnitude comparator (16/16), 4-way multiplexer (64/64).
* 1-bit full adder as **pure wires** — 8/8.
* All six gate kinds, 8-bit, as **pure wires** — 65,536/65,536.
* 8-bit adder with `Cin` tied, as **pure wires** — 65,536/65,536.
* Clocked: 2-bit counter, 8-bit register hold, 4-bit accumulator, divide-by-2.
* Component knowledge base: 74/74 components, 322 attributes, 1,402 geometry sweeps, cross-verified.

**Known limitations, recorded rather than hidden**

* Automatic routing verified for single-bit combinational circuits only. The 8-bit ALU re-routed to
  pure wires passes 725,572/1,048,576 — **not shipped as a verified artifact**.
* Behaviour simulated for 10/74 components.
* Floating-point behaviour unmeasured.
* `seqmodel.py` `JK Flip-Flop` / `SR Flip-Flop` names are wrong; those components are dropped.
* `ROM` not modelled; RAM contents cannot be preloaded.

**Not included on purpose**

* The Logisim Evolution JAR (52 MB, third-party, GPL) — install it yourself, see `docs/installation.md`.
* The user's private `.circ` reference samples — no public licence.
* Research material, probe programs and intermediate build output.
