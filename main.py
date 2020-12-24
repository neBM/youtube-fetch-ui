import threading, http.server, urllib.parse, json, traceback, subprocess, logging, os, csv, getopt, sys
import googleapiclient.discovery, googleapiclient.errors

queue = dict()
outputPath = "./media/[%(uploader_id)s]/[%(playlist_id)s]/[%(id)s].%(ext)s"

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
            response_code, headers, data = API.process_request(method, urlcomps, qs)

            self.send_response(response_code)
            for header in headers:
                self.send_header(header, headers[header])
            self.end_headers()
            self.wfile.write(data.encode())

class API:
    @staticmethod
    def process_request(method, urlcomps, qs):
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
                    "getQueue": cls.Commands.get_queue,
                    "getHistory": cls.Commands.get_history
                },
                "POST": {
                    "addUrl": cls.Commands.add_url
                },
                "DELETE": {
                    "removeItem": cls.Commands.remove_item
                }
            }[method][path[1]]
        except KeyError:
            raise ValueError("Command not found!")

    @staticmethod
    def google_api_auth():
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
    def get_videos(plid):
        youtube = API.google_api_auth()

        request = youtube.playlistItems().list(
            part="contentDetails",
            playlistId=plid
        )

        response = request.execute()

        return response["items"]

    @staticmethod
    def get_video(vid):
        youtube = API.google_api_auth()

        request = youtube.videos().list(
            part="snippet",
            id=",".join(vid)
        )


        response = request.execute()

        return {x["id"]: x for x in response["items"]}

    @staticmethod
    def get_playlist_info(plid):
        youtube = API.google_api_auth()

        request = youtube.playlists().list(
            part="snippet",
            id=plid
        )

        response = request.execute()

        return response["items"][0]

    @staticmethod
    def write_history(url, name, chname):
        writer = csv.writer(open("history.csv", "a", newline="", encoding='utf-8'))
        writer.writerow([url, name, chname])


    class Commands:
        @staticmethod
        def get_queue(urlcomps, qs):
            return (http.HTTPStatus.OK, {
                "content-type": "application/json"
            }, json.dumps({"status": "OK", "queue": queue}))

        @staticmethod
        def get_history(urlcomps, qs):
            return (http.HTTPStatus.OK, {
                "content-type": "application/json"
            }, json.dumps({"status": "OK", "history": {
                r[0]: {
                    "chName": r[2],
                    "plName": r[1]
                } for r in csv.reader(open("history.csv", "r"))
            }}))

        @staticmethod
        def add_url(urlcomps, qs):
            url = qs["url"][0]
            ytqs = urllib.parse.parse_qs(urllib.parse.urlparse(url)[4])
            if "v" in ytqs.keys():
                vid = ytqs["v"][0]
                vinfo = API.get_video([vid])[vid]
                videos = {vinfo["id"]: vinfo}
                API.write_history(url, vinfo["snippet"]["title"], vinfo["snippet"]["channelTitle"])
            elif "list" in ytqs.keys():
                plid = ytqs["list"][0]
                plir = API.get_videos(plid)
                videos = API.get_video([x["contentDetails"]["videoId"] for x in plir])
                
                
                plinfo = API.get_playlist_info(plid)
                API.write_history(url, plinfo["snippet"]["title"], None)
            else:
                raise ValueError("Missing list or video id in {}".format(ytqs.keys()))

            for vid in videos: DownloadWorker.append(videos[vid]["id"], videos[vid]["snippet"]["title"])
            return (http.HTTPStatus.OK, {"content-type": "application/json"}, json.dumps({"status": "OK"}))

        @staticmethod
        def remove_item(urlcomps, qs):
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
            self.do_work(vid)

    @classmethod
    def append(cls, vid, name):
        queue[vid] = name
        with cls.c:
            cls.c.notify()

    @classmethod
    def remove(cls, vid):
        del queue[vid]
    
    def do_work(self, vid):
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
        return_code = p.wait()
        if return_code != 0:
            logging.error("Non 0 return code!")

if __name__ == "__main__":
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
