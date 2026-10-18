# Common developer tasks. Uses the virtualenv in .venv if present.

VENV ?= .venv
PYTHON = $(VENV)/bin/python
PIP = $(VENV)/bin/pip

.PHONY: deps migrate test css check server

deps:
	$(PIP) install -r requirements-dev.txt

migrate:
	$(PYTHON) manage.py migrate

server:
	$(PYTHON) manage.py runserver

test:
	$(PYTHON) -m pytest tests/

check:
	$(PYTHON) manage.py check

css:
	./bin/tailwindcss -i static/css/src/input.css -o static/css/app.css --minify
	# Or with Node: npm run build-css
