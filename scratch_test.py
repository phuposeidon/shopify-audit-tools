import sys
import typer

app = typer.Typer(name="audit")

@app.command(name="audit")
def audit_url(url: str):
    print("Called audit_url with", url)

def main():
    print("Original sys.argv:", sys.argv)
    if len(sys.argv) >= 2 and sys.argv[1].startswith("http"):
        sys.argv.insert(1, "audit")
    print("Modified sys.argv:", sys.argv)
    app()

if __name__ == "__main__":
    main()
