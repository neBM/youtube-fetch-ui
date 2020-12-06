import threading, http.server, urllib.parse, json, traceback, subprocess, logging, os, csv, getopt, sys
import googleapiclient.discovery
import googleapiclient.errors

queue = dict()

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
        except Exception:
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
    def googleApiAuth():
        # Disable OAuthlib's HTTPS verification when running locally.
        # *DO NOT* leave this option enabled in production.
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

        api_service_name = "youtube"
        api_version = "v3"

        with open("./apiKey.txt", "r") as f:
            key = f.readline()

        # Get credentials and create an API client
        return googleapiclient.discovery.build(api_service_name, api_version, developerKey=key, cache_discovery=False)


    @staticmethod
    def getVideos(plid):
        youtube = API.googleApiAuth()

        request = youtube.playlistItems().list(
            part="contentDetails",
            playlistId=plid
        )

        response = request.execute()

        return response["items"]

    @staticmethod
    def getVideo(vid):
        youtube = API.googleApiAuth()

        request = youtube.videos().list(
            part="snippet",
            id=",".join(vid)
        )


        response = request.execute()

        return {x["id"]: x for x in response["items"]}

    @staticmethod
    def getPlaylistInfo(plid):
        youtube = API.googleApiAuth()

        request = youtube.playlists().list(
            part="snippet",
            id=plid
        )

        response = request.execute()

        return response["items"][0]

    @staticmethod
    def writeHistory(url, name, chname):
        writer = csv.writer(open("history.csv", "a", newline="", encoding='utf-8'))
        writer.writerow([url, name, chname])


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
            ytqs = urllib.parse.parse_qs(urllib.parse.urlparse(url)[4])
            if "v" in ytqs.keys():
                vid = ytqs["v"][0]
                vinfo = API.getVideo([vid])[vid]
                videos = {vinfo["id"]: vinfo}
                API.writeHistory(url, vinfo["snippet"]["title"], vinfo["snippet"]["channelTitle"])
            elif "list" in ytqs.keys():
                plid = ytqs["list"][0]
                plir = API.getVideos(plid)
                videos = API.getVideo([x["contentDetails"]["videoId"] for x in plir])
                
                
                plinfo = API.getPlaylistInfo(plid)
                API.writeHistory(url, plinfo["snippet"]["title"], None)
            else:
                raise Exception("Missing list or video id in {}".format(ytqs.keys()))

            for vid in videos: DownloadWorker.append(videos[vid]["id"], videos[vid]["snippet"]["title"])
            return (http.HTTPStatus.OK, {"content-type": "application/json"}, json.dumps({"status": "OK"}))

        @staticmethod
        def removeItem(urlcomps, qs):
            DownloadWorker.remove(qs["vid"][0])
            return (http.HTTPStatus.OK, {"content-type": "application/json"}, json.dumps({"status": "OK"}))

class DownloadWorker:
    c = threading.Condition()
    def run(self):
        while True:
            with self.c:
                while len(queue) <= 0:
                    logging.debug("Download worker waiting")
                    self.c.wait()
            vid = list(queue.keys())[0]
            name = queue.pop(vid)
            logging.debug("Downloading {}".format(name))
            self.doWork(vid)

    @classmethod
    def append(cls, vid, name):
        queue[vid] = name
        with cls.c:
            cls.c.notify()

    @classmethod
    def remove(cls, vid):
        del queue[vid]
    
    def doWork(self, vid):
        p = subprocess.Popen(
            [
                "youtube-dl",
                "--yes-playlist",
                "--cookies", "cookies.txt",
                "-o", outputPath,
                "-f", "bestvideo+bestaudio",
                vid
            ]
        )
        
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

logging.basicConfig(level=logging.DEBUG)

exportDir = "./media/"
outputFormat = "[%(uploader_id)s]/[%(playlist_id)s]/[%(id)s].%(ext)s"
opts, args = getopt.getopt(sys.argv[1:], "e:f:")
for opt, arg in opts:
    if opt == "-e":
        exportDir = arg
    elif opt == "-f":
        outputFormat = arg

outputPath = os.path.join(exportDir, outputFormat)
outputPath = os.path.abspath(outputPath)
logging.info("Using: {}".format(outputPath))

httpServerWorker = HttpServerWorker()
threading.Thread(target=httpServerWorker.run, name="HttpServerWorker").start()

downloadWorker = DownloadWorker()
threading.Thread(target=downloadWorker.run, name="DownloadWorker").start()
        returnCode = p.wait()
        if returnCode != 0:
            logging.error("Non 0 return code!")
