import threading, http.server, urllib.parse, json, traceback, subprocess, logging, os, csv, getopt, sys
import googleapiclient.discovery
import googleapiclient.errors

queue = []

class HttpServerWorker:
    def run(self):
        server = http.server.ThreadingHTTPServer
        handler = self.HttpRequestHandler
        port = 5151

        with server(("", port), handler) as httpd:
            httpd.serve_forever()

    class HttpRequestHandler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            urlcomps = urllib.parse.urlparse(self.path)
            path = urlcomps[2].split("/")[1:]
            if path[0] == "www":
                super().do_GET()
            elif path[0] == "api":
                self.do_APIRequest("GET", urlcomps)
            else:
                self.send_response(http.HTTPStatus.NOT_FOUND)
                self.end_headers()

        def do_POST(self):
            urlcomps = urllib.parse.urlparse(self.path)
            path = urlcomps[2].split("/")[1:]
            if path[0] == "api":
                self.do_APIRequest("POST", urlcomps)
            else:
                self.send_response("404")
                self.end_headers()
        
        def do_DELETE(self):
            urlcomps = urllib.parse.urlparse(self.path)
            path = urlcomps[2].split("/")[1:]
            if path[0] == "api":
                self.do_APIRequest("DELETE", urlcomps)
            else:
                self.send_response("404")
                self.end_headers()

        def do_APIRequest(self, method, urlcomps):
            if method == "GET":
                qs = urllib.parse.parse_qs(urlcomps[4])
            elif method == "POST":
                qs = urllib.parse.parse_qs(self.rfile.read(int(self.headers.get("content-length"))).decode())
            elif method == "DELETE":
                qs = urllib.parse.parse_qs(self.rfile.read(int(self.headers.get("content-length"))).decode())
            responseCode, headers, data = API.processRequest(method, urlcomps, qs)

            self.send_response(responseCode)
            for header in headers:
                self.send_header(header, headers[header])
            self.end_headers()
            self.wfile.write(data.encode())

class API:
    @staticmethod
    def processRequest(method, urlcomps, qs):
        try:
            delegate = API.parse(method, urlcomps)
            return delegate(urlcomps, qs)
        except Exception as e:
            return (
                http.HTTPStatus.INTERNAL_SERVER_ERROR,
                {
                    "content-type": "application/json"
                },
                json.dumps(
                    {
                        "status": "failed",
                        "exception": traceback.format_exc()
                    }
                )
            )


    @classmethod
    def parse(cls, method, urlcomps):
        try:
            path = urlcomps[2].split("/")[1:]
            return {
                "GET": {
                    "getQueue": cls.Commands.getQueue,
                    "getHistory": cls.Commands.getHistory
                },
                "POST": {
                    "addUrl": cls.Commands.addUrl
                },
                "DELETE": {
                    "removeItem": cls.Commands.removeItem
                }
            }[method][path[1]]
        except KeyError:
            raise Exception("Command not found!")

    @staticmethod
    def getPlaylistInfo(plid):

        # Disable OAuthlib's HTTPS verification when running locally.
        # *DO NOT* leave this option enabled in production.
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

        api_service_name = "youtube"
        api_version = "v3"

        with open("./apiKey.txt", "r") as f:
            key = f.readline()

        # Get credentials and create an API client
        youtube = googleapiclient.discovery.build(
            api_service_name, api_version, developerKey=key, cache_discovery=False)

        request = youtube.playlists().list(
            part="snippet",
            id=plid
        )
        response = request.execute()

        return response

    class Commands:
        @staticmethod
        def getQueue(urlcomps, qs):
            return (http.HTTPStatus.OK, {
                "content-type": "application/json"
            }, json.dumps({"status": "OK", "queue": queue}))

        @staticmethod
        def getHistory(urlcomps, qs):
            return (http.HTTPStatus.OK, {
                "content-type": "application/json"
            }, json.dumps({"status": "OK", "history": {
                r[0]: {
                    "chName": r[2],
                    "plName": r[1]
                } for r in csv.reader(open("history.csv", "r"))
            }}))

        @staticmethod
        def addUrl(urlcomps, qs):
            url = qs["url"][0]
            response = API.getPlaylistInfo(urllib.parse.parse_qs(urllib.parse.urlparse(url)[4])["list"][0])
            plName = response['items'][0]['snippet']['title']
            chName = response['items'][0]['snippet']['channelTitle']
            DownloadWorker.append(url, plName, chName)
            return (http.HTTPStatus.OK, {"content-type": "application/json"}, json.dumps({"status": "OK"}))

        @staticmethod
        def removeItem(urlcomps, qs):
            queue.remove(qs["url"][0])
            return (http.HTTPStatus.OK, {"content-type": "application/json"}, json.dumps({"status": "OK"}))

class DownloadWorker:
    c = threading.Condition()
    def run(self):
        while True:
            with self.c:
                while len(queue) <= 0:
                    logging.debug("Download worker waiting")
                    self.c.wait()
            url = queue.pop(0)
            logging.debug("Downloading {}".format(url))
            self.doWork(url)

    @classmethod
    def append(cls, url, plName, chName):
        writer = csv.writer(open("history.csv", "a", newline="", encoding='utf-8'))
        writer.writerow([url, plName, chName])
        queue.append(url)
        with cls.c:
            cls.c.notify()

    @classmethod
    def remove(cls, url):
        queue.remove(url)
    
    def doWork(self, url):
        p = subprocess.Popen(
            [
                "youtube-dl",
                "--yes-playlist",
                "--cookies", "cookies.txt",
                "-o", outputPath,
                "-f", "bestvideo+bestaudio",
                url
            ]
        )
        p.wait()
        
try:
    subprocess.Popen(["ffmpeg", "-version"], stdout=subprocess.DEVNULL).wait()
except FileNotFoundError:
    print("Missing ffmpeg!")
    quit(-1)
try:
    subprocess.Popen(["youtube-dl", "--version"], stdout=subprocess.DEVNULL).wait()
except FileNotFoundError:
    print("Missing youtube-dl!")
    quit(-1)
if not os.path.exists("./apiKey.txt"):
    print("Missing apiKey.txt!")
    quit(-1)

exportDir = "./media/"
outputFormat = "[%(uploader_id)s]/[%(playlist_id)s]/[%(id)s].%(ext)s"
opts, args = getopt.getopt(sys.argv[1:], "e:f:")
for opt, arg in opts:
    if opt == "-e":
        exportDir = arg
    elif opt == "-f":
        outputFormat = arg

outputPath = os.path.join(exportDir, outputFormat)
print("Using: {}".format(outputPath))

logging.basicConfig(level=logging.DEBUG)

httpServerWorker = HttpServerWorker()
threading.Thread(target=httpServerWorker.run, name="HttpServerWorker").start()

downloadWorker = DownloadWorker()
threading.Thread(target=downloadWorker.run, name="DownloadWorker").start()
