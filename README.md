# Deployment

Copy .env.local to .env and fill in all the required fields

```
docker build -t comailer .
docker run comailer # docker run -d comailer -p 8501:8501
```
