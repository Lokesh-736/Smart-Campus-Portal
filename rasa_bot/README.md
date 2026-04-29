## Smart Campus Rasa Assistant

This folder contains the Rasa assistant that powers the website chat widget.

### Run (Windows / Docker recommended)

From the project root:

```bash
docker compose -f docker-compose.rasa.yml up --build
```

Then train the model (first time, or after editing training data):

```bash
docker compose -f docker-compose.rasa.yml run --rm rasa train
```

Rasa API will be available at `http://localhost:5005`.

### How the website talks to Rasa

The Flask app sends messages to Rasa via `/chat` (proxy route), including metadata:

- `role`, `username` (from session)
- `page_path`, `page_title` (from browser)

The Rasa action server reads `database.db` (mounted read-only) to answer:

- schedules
- notes
- teachers
- students
- subjects

