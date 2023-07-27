import logging


def generate_refresh_files():
    from pathlib import Path

    template = Path("refresh/template").open().read()
    logger = logging.getLogger('Refresh')

    for python_file in Path('refresh').glob('*.py'):
        if python_file.name == 'generate_refresh.py':
            continue
        logger.info(f"Generating refresh file for {python_file.name}")
        clean_name = python_file.name[1:]
        with open(clean_name, 'w') as f:
            f.write(template.format(python_file.stem))


if __name__ == '__main__':
    import os

    os.chdir('..')
    generate_refresh_files()
