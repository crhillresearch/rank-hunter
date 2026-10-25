from rank42.agent_math import renderable_agent_math


def test_agent_math_renders_mazur_reply() -> None:
    reply = r"Over (\mathbb Q), cyclic (C_n) for (n=1,\dots,10,12), or (C_2\times C_{2m})."
    assert renderable_agent_math(reply) == (
        r"Over $\mathbb Q$, cyclic $C_n$ for $n=1,\dots,10,12$, "
        r"or $C_2\times C_{2m}$."
    )


def test_agent_math_converts_latex_delimiters() -> None:
    assert renderable_agent_math(r"\(C_n\) and \[C_2\times C_{2m}\]") == (
        "$C_n$ and \n$$\nC_2\\times C_{2m}\n$$\n"
    )


def test_agent_math_preserves_code_and_existing_math() -> None:
    reply = "`(\\mathbb Q)` and $C_n$ and (plain text)\n```tex\n(\\mathbb Q)\n```"
    assert renderable_agent_math(reply) == reply
