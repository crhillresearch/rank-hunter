"""Start Streamlit after Sage has initialized its process-wide signal hooks."""


def main() -> None:
    # Streamlit executes the application script in a worker thread.  Importing
    # Sage there makes cysignals attempt to register handlers outside the main
    # interpreter thread, which Python rejects.  Initialize Sage first, while
    # this launcher is still running on the process main thread.
    import sage.all  # noqa: F401
    from streamlit.web import cli as streamlit_cli

    streamlit_cli.main(prog_name="streamlit")


if __name__ == "__main__":
    main()
