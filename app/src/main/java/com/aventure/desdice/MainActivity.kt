package com.aventure.desdice

import android.annotation.SuppressLint
import android.net.Uri
import android.os.Bundle
import android.util.Base64
import android.webkit.JavascriptInterface
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import org.json.JSONObject
import java.io.ByteArrayInputStream

/**
 * Toute l'application vit ici : au lancement, on prepare l'app Flask
 * existante (dice_web.py) en memoire (aucun serveur reseau), puis on
 * charge une adresse purement virtuelle dans une WebView integree a
 * l'appli. Chaque requete de la page est interceptee et simulee cote
 * Python via android_bridge.handle_request(), sans jamais ouvrir de
 * socket reseau -- donc plus aucun port a coordonner entre deux
 * versions de l'appli installees en parallele.
 *
 * Deux chemins d'interception, car l'API Android ne donne pas acces au
 * corps d'une requete POST au niveau natif (voir android_bridge.py) :
 *   - GET/HEAD -> shouldInterceptRequest() (natif, cote Kotlin)
 *   - POST     -> script JS injecte en tout debut de page, qui surcharge
 *                 fetch() et les soumissions de <form>, et transmet le
 *                 corps de la requete (y compris les fichiers) a Python
 *                 via l'interface JS AndroidBridge ci-dessous.
 */
class MainActivity : AppCompatActivity() {

    private lateinit var webView: WebView
    // Hote purement virtuel : jamais reellement contacte, on l'utilise
    // seulement pour reconnaitre "c'est une requete vers notre appli" et
    // la faire passer par handle_request() au lieu du reseau.
    private val virtualHost = "127.0.0.1"
    private val virtualUrl = "http://$virtualHost/"
    // addDocumentStartJavaScript() attend une regle d'ORIGINE (schema +
    // hote + port eventuel), jamais un chemin ni de wildcard apres un
    // "/". "http://127.0.0.1/" est donc invalide (IllegalArgumentException
    // au lancement) -- il faut l'origine seule, sans slash final. Une
    // regle d'origine s'applique deja a toutes les URL de cette origine,
    // quel que soit le chemin, donc une seule entree suffit.
    private val virtualOrigin = "http://$virtualHost"

    // --- Selecteur de fichiers pour les <input type="file"> de la page web ---
    private var fileChooserCallback: ValueCallback<Array<Uri>>? = null

    private val fileChooserLauncher =
        registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
            val data = result.data
            val uris: Array<Uri> = when {
                data == null || result.resultCode != RESULT_OK -> emptyArray()
                data.clipData != null -> Array(data.clipData!!.itemCount) { i ->
                    data.clipData!!.getItemAt(i).uri
                }
                data.data != null -> arrayOf(data.data!!)
                else -> emptyArray()
            }
            fileChooserCallback?.onReceiveValue(uris)
            fileChooserCallback = null
        }

    /**
     * Interface exposee a la page web. Appelee par le script JS injecte
     * (voir BRIDGE_SCRIPT) pour toute requete que shouldInterceptRequest
     * ne peut pas gerer (POST avec corps). Chaquopy appelle Python de
     * facon synchrone et bloquante -- c'est voulu : la promesse fetch()
     * cote JS attend le resultat, exactement comme pour une vraie
     * requete reseau.
     */
    private inner class AndroidBridge {
        @JavascriptInterface
        fun request(method: String, path: String, headersJson: String, bodyBase64: String): String {
            return Python.getInstance()
                .getModule("android_bridge")
                .callAttr("handle_request", method, path, headersJson, bodyBase64)
                .toString()
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        if (!Python.isStarted()) {
            Python.start(AndroidPlatform(this))
        }
        Python.getInstance()
            .getModule("android_bridge")
            .callAttr("start_server")
        // Plus de socket a attendre : des que start_server() revient,
        // dice_web est importe et handle_request() peut deja servir des
        // requetes. Chargement immediat, sans delai ni tentatives.

        webView = findViewById(R.id.webview)
        webView.settings.javaScriptEnabled = true
        webView.settings.domStorageEnabled = true
        webView.settings.allowFileAccess = true
        webView.addJavascriptInterface(AndroidBridge(), "AndroidBridge")

        if (WebViewFeature.isFeatureSupported(WebViewFeature.DOCUMENT_START_SCRIPT)) {
            WebViewCompat.addDocumentStartJavaScript(
                webView, BRIDGE_SCRIPT, setOf(virtualOrigin)
            )
        }
        // Necessite la dependance androidx.webkit:webkit (voir build.gradle).
        // Sans cette dependance, les POST (actions de jeu, upload d'image)
        // ne seront pas interceptes : shouldInterceptRequest seul ne peut
        // pas lire leur corps.

        webView.webViewClient = object : WebViewClient() {
            override fun shouldInterceptRequest(
                view: WebView?,
                request: WebResourceRequest?
            ): WebResourceResponse? {
                val req = request ?: return super.shouldInterceptRequest(view, request)
                if (req.url.host != virtualHost) {
                    return super.shouldInterceptRequest(view, request)
                }
                if (req.method != "GET" && req.method != "HEAD") {
                    // Ne devrait normalement jamais arriver : le script JS
                    // intercepte les POST avant qu'ils ne deviennent une
                    // vraie requete. Filet de securite seulement.
                    return super.shouldInterceptRequest(view, request)
                }
                return try {
                    serveRequest(req.method, req.url.path ?: "/", req.requestHeaders)
                } catch (e: Exception) {
                    null
                }
            }
        }
        webView.webChromeClient = object : WebChromeClient() {
            override fun onShowFileChooser(
                view: WebView?,
                filePathCallback: ValueCallback<Array<Uri>>,
                fileChooserParams: FileChooserParams?
            ): Boolean {
                fileChooserCallback = filePathCallback
                val intent = fileChooserParams?.createIntent()?.apply {
                    type = "image/*"
                }
                if (intent == null) {
                    fileChooserCallback = null
                    filePathCallback.onReceiveValue(null)
                    return false
                }
                fileChooserLauncher.launch(intent)
                return true
            }
        }

        webView.loadUrl(virtualUrl)
    }

    /** Simule une requete GET/HEAD via android_bridge.py et construit la reponse WebView. */
    private fun serveRequest(
        method: String,
        path: String,
        requestHeaders: Map<String, String>
    ): WebResourceResponse {
        val headersJson = JSONObject(requestHeaders as Map<*, *>).toString()
        val resultJson = Python.getInstance()
            .getModule("android_bridge")
            .callAttr("handle_request", method, path, headersJson, "")
            .toString()
        val result = JSONObject(resultJson)
        val bodyBytes = Base64.decode(result.getString("body_b64"), Base64.DEFAULT)
        val headers = result.getJSONObject("headers")

        val contentType = headers.optString("Content-Type", "text/html; charset=utf-8")
        val mimeType = contentType.substringBefore(";").trim().ifEmpty { "text/html" }
        val charset = if (contentType.contains("charset=")) {
            contentType.substringAfter("charset=").trim()
        } else {
            "utf-8"
        }

        val responseHeaders = HashMap<String, String>()
        headers.keys().forEach { k ->
            if (k.lowercase() != "content-type") responseHeaders[k] = headers.getString(k)
        }

        val response = WebResourceResponse(mimeType, charset, ByteArrayInputStream(bodyBytes))
        response.responseHeaders = responseHeaders
        val status = result.getInt("status")
        val reason = if (status in 200..299) "OK" else "ERROR"
        response.setStatusCodeAndReasonPhrase(status, reason)
        return response
    }

    override fun onBackPressed() {
        if (webView.canGoBack()) {
            webView.goBack()
        } else {
            super.onBackPressed()
        }
    }

    companion object {
        /**
         * Injecte avant tout script de la page (document-start). Surcharge
         * fetch() et l'evenement submit des <form> pour les methodes autres
         * que GET/HEAD : encode le corps (texte, Blob, ou FormData -- y
         * compris les fichiers, via l'astuce new Response(formData) qui
         * genere elle-meme un multipart/form-data valide) et le transmet a
         * Python via AndroidBridge.request(), de facon synchrone.
         *
         * Les GET/HEAD sont laisses passer tels quels : ils sont geres
         * nativement par shouldInterceptRequest() cote Kotlin.
         */
        private const val BRIDGE_SCRIPT = """
(function () {
  if (window.__diceBridgeInstalled) return;
  window.__diceBridgeInstalled = true;

  async function encodeBody(body) {
    if (body == null) return { bytes: new ArrayBuffer(0), contentType: null };
    if (body instanceof FormData) {
      var r = new Response(body);
      var buf = await r.arrayBuffer();
      return { bytes: buf, contentType: r.headers.get('content-type') };
    }
    if (body instanceof Blob) {
      return { bytes: await body.arrayBuffer(), contentType: body.type || null };
    }
    if (body instanceof ArrayBuffer) return { bytes: body, contentType: null };
    if (typeof body === 'string') {
      return { bytes: new TextEncoder().encode(body).buffer, contentType: null };
    }
    return { bytes: new TextEncoder().encode(String(body)).buffer, contentType: null };
  }

  function toBase64(buf) {
    var bytes = new Uint8Array(buf);
    var binary = '';
    var chunk = 0x8000;
    for (var i = 0; i < bytes.length; i += chunk) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
    }
    return btoa(binary);
  }

  function fromBase64(b64) {
    var binary = atob(b64);
    var bytes = new Uint8Array(binary.length);
    for (var i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return bytes.buffer;
  }

  async function bridgeRequest(method, url, headersObj, bodyBuf) {
    var path = new URL(url, location.href).pathname + new URL(url, location.href).search;
    var resultJson = window.AndroidBridge.request(
      method, path, JSON.stringify(headersObj), toBase64(bodyBuf)
    );
    return JSON.parse(resultJson);
  }

  var origFetch = window.fetch;
  window.fetch = async function (input, init) {
    var url = typeof input === 'string' ? input : input.url;
    var method = ((init && init.method) || (input && input.method) || 'GET').toUpperCase();
    if (method === 'GET' || method === 'HEAD') return origFetch(input, init);

    var headersObj = {};
    var initHeaders = (init && init.headers) || (input && input.headers);
    if (initHeaders) new Headers(initHeaders).forEach(function (v, k) { headersObj[k] = v; });

    var bodyIn = (init && init.body) || null;
    var enc = await encodeBody(bodyIn);
    if (enc.contentType && !headersObj['Content-Type'] && !headersObj['content-type']) {
      headersObj['Content-Type'] = enc.contentType;
    }

    var result = await bridgeRequest(method, url, headersObj, enc.bytes);
    return new Response(fromBase64(result.body_b64), {
      status: result.status,
      headers: result.headers
    });
  };

  document.addEventListener('submit', function (ev) {
    var form = ev.target;
    if (!(form instanceof HTMLFormElement)) return;
    var method = (form.getAttribute('method') || 'GET').toUpperCase();
    if (method === 'GET') return; // gere nativement, on laisse faire

    ev.preventDefault();
    var action = form.getAttribute('action') || location.pathname;
    var formData = new FormData(form);
    (async function () {
      var enc = await encodeBody(formData);
      var headersObj = {};
      if (enc.contentType) headersObj['Content-Type'] = enc.contentType;
      var result = await bridgeRequest(method, action, headersObj, enc.bytes);
      var ct = (result.headers && (result.headers['Content-Type'] || result.headers['content-type'])) || '';
      if (ct.indexOf('text/html') !== -1) {
        var html = new TextDecoder('utf-8').decode(fromBase64(result.body_b64));
        document.open();
        document.write(html);
        document.close();
        window.__diceBridgeInstalled = false; // le script sera reinjecte au prochain document-start
      }
    })();
  }, true);
})();
"""
    }
}
