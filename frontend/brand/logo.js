/* Official ERAP mark.
   erap-logo.png is a crop of ERAP-logo-4K.png. The artwork is unchanged. */

const ERAP_LOGO_SRC = new URL("erap-logo.png", document.currentScript.src).href;


class ERAPLogo extends HTMLElement {

    connectedCallback() {

        if (this.querySelector("img")) {

            return;

        }

        const image = document.createElement("img");

        image.className = "erap-logo";
        image.alt = "ERAP";
        image.src = ERAP_LOGO_SRC;
        image.decoding = "async";
        this.appendChild(image);

    }

}


customElements.define("erap-logo", ERAPLogo);
