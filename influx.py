from influxdb_client import InfluxDBClient

client = InfluxDBClient(url="http://192.168.0.112:8086", token='appdaemon-dev:opennow', org='-')
query_api = client.query_api()
