.PHONY: docker-up docker-down docker-logs docker-test py-test clean

docker-up:
	docker compose up --build -d

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f

docker-test:
	python test_cluster.py --config cluster_config.docker.host.json

py-test:
	python -m py_compile node.py manager_app.py client.py test_cluster.py healthcheck_node.py healthcheck_manager.py

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
