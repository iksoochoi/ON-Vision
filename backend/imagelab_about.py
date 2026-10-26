"""The explanations the Image Lab page shows above the pictures: for every technique what it does, how to read the result and
what it is good for in this project (the shoe pictures, the QR labels), and for every value what it changes.
Text only: backend/imagelab.py puts it into METHODS. `code` marks a name, **bold** an emphasis (the page shows both)."""

ABOUT = {
    "original": {
        "what": "Shows the picture as it is, with no processing (only the crop and the size on screen are applied).",
        "read": "If it looks the same as the original on the left, everything is fine.",
        "use": "The reference for comparing the other techniques. Also use it with `Crop %` to cut out just a QR label and look at it enlarged.",
    },
    "grayscale": {
        "what": "Drops the colors and keeps only the brightness: a black and white picture.",
        "read": "Bright places are white, dark places are black.",
        "use": "QR reading and the sharpness score both work on this brightness. Check that the white and black cells of the label are clearly different here. If all the white cells are the same white, the label is **blown out** (overexposed).",
    },
    "invert": {
        "what": "Swaps bright and dark.",
        "read": "The white label turns dark, the dark QR cells turn white.",
        "use": "To see whether a QR reader copes with a light code on a dark ground as well as the usual dark on light, and to make white objects on the dark belt (the jig, the label) stand out.",
    },
    "hsv": {
        "what": "Splits the color into hue (H), saturation (S) and value (V) and shows those numbers as colors.",
        "read": "The colors on screen are not the real colors but the values. Objects of one color get similar colors (the teal jig, the green belt strips, the yellow line).",
        "use": "To find the limits for picking out an object by its color, such as the teal jig or a red sticker. The red arrow stickers in the PoC pictures were found with these values.",
    },
    "histogram_equalization": {
        "what": "Spreads the brightness over the whole range, so that dark and bright places differ more.",
        "read": "Everything gets clearer, but noise and glare grow too.",
        "use": "To look for hidden shapes (dirt, writing) in a picture that came out dark. It treats the whole picture alike, so it cannot help much when only a part is blown out.",
    },
    "clahe": {
        "what": "Cuts the picture into small tiles and raises the contrast of each tile on its own (local contrast).",
        "read": "Even with shadows and glare, the black and white cells inside a tile show up again.",
        "use": "One of the things the QR reading tries on a code it could not read. Look whether the code cells of a label that is partly dark or glared become clear.",
    },
    "gaussian": {
        "what": "Mixes each pixel with its neighbours: a soft blur.",
        "read": "Noise is reduced, but edges get soft too. The larger the Kernel, the more blur.",
        "use": "For pictures with a lot of grain (high ISO): reduce the noise, then see how edges or a threshold change. The sharpness score reacts strongly to this kind of blur.",
    },
    "median": {
        "what": "Replaces each pixel by the median of its neighbours, which removes speckles.",
        "read": "Edges stay sharper than with the Gaussian blur, and the grain and speckles are gone.",
        "use": "To remove the grain of an ISO 800 picture and test whether the QR reads better. If thin QR cells get smeared, lower the Kernel.",
    },
    "bilateral": {
        "what": "A noise filter that keeps the edges and smooths only the flat places.",
        "read": "The fine noise of the knit and the belt goes down, while the outlines of logos and labels stay.",
        "use": "To compare edges after noise removal. It is slow: crop, or lower `Show at`, for big pictures.",
    },
    "canny": {
        "what": "Picks out only the places where the brightness changes sharply (edges) as thin lines.",
        "read": "The outline of the shoe, the logos and the edges of the label come out as lines. Where the picture is blurred the lines break or get weak.",
        "use": "To check that the edges of the label and its QR cells are alive (a QR that cannot be read has no edges). Good for before / after comparisons of a blur.",
    },
    "sobel": {
        "what": "Shows how strong the brightness changes are at each place (the size of the gradient).",
        "read": "The brighter, the stronger the edge.",
        "use": "To find places where focus or a blur weakened the edges. For one direction at a time use the vertical and horizontal edges below.",
    },
    "vertical_edges": {
        "what": "Shows only edges that run up and down (where the brightness changes from left to right).",
        "read": "The vertical borders of the QR cells and the vertical strokes of letters.",
        "use": "The belt moves along the width of the picture, and a **horizontal motion blur erases the vertical edges first.** If this result is much weaker than the horizontal edges, the blur is horizontal (the same idea as the motion blur check).",
    },
    "horizontal_edges": {
        "what": "Shows only edges that run left and right (where the brightness changes from top to bottom).",
        "read": "The horizontal borders of the QR cells, the upper line of the midsole, the horizontal lines of the belt.",
        "use": "Compare with the vertical edges to tell the direction of a blur. If the vertical edges are much weaker, the blur is horizontal.",
    },
    "laplacian": {
        "what": "Shows how sharp the edges and the fine texture are (the second derivative).",
        "read": "Places that are in focus and not blurred are bright. The knit texture of the shoe answers everywhere.",
        "use": "The sharpness score of the app is the variance of this. Use it to compare pictures of the same camera: which is sharper, is the label blurred. Gain only changes how bright the picture is drawn.",
    },
    "contours": {
        "what": "Finds the connected outlines in the Canny edges and draws them in green.",
        "read": "The outer lines of objects appear as green lines; the number of contours is in the result line below.",
        "use": "To see whether the shape of the shoe, the label or a logo is found as a closed line. This is the starting point for finding a label or logo by its shape (a rule check for print defects).",
    },
    "hough_lines": {
        "what": "Finds straight lines in the edges and draws them.",
        "read": "Green lines are all directions, yellow horizontal, pink vertical. The number of lines is in the result line below.",
        "use": "To see how much the belt edge, the jig or the edge of the label is tilted (the angle). A tilted label needs the straightening step of the QR reading.",
    },
    "morph_lines": {
        "what": "Keeps only structures that run on in one direction (horizontal or vertical) for a given length.",
        "read": "White lines are left where something is long in that direction.",
        "use": "To separate belt wrinkles and stripes, the yellow belt line, or stripes made by a horizontal blur.",
    },
    "threshold": {
        "what": "Makes every pixel white if it is brighter than the limit, black if not.",
        "read": "The white part is everything brighter than the limit.",
        "use": "With the limit near 250 it shows the places that are **blown out** (saturated). Use it to see whether the white cells of a label are blown out, and how far the white midsole goes.",
    },
    "adaptive_threshold": {
        "what": "Makes black and white with a limit from the neighbourhood of each place.",
        "read": "Even with uneven light, the dark and bright cells of a label come out separated.",
        "use": "One of the things the QR reading tries. If the QR cells separate cleanly into black and white the code can be read; if they are smeared, blur or noise is the cause.",
    },
    "otsu": {
        "what": "Finds by itself the limit that separates the picture best into black and white.",
        "read": "The limit it found is in the result line below.",
        "use": "One of the binarisations the QR reading uses. When the light differs a lot across the picture, the adaptive threshold does better.",
    },
    "morph_open": {
        "what": "Makes black and white with Otsu, then removes small white dots (opening).",
        "read": "White pieces smaller than the Kernel are gone, only the large shapes stay.",
        "use": "To drop specks and look only at the large structures (the body of the shoe, a logo).",
    },
    "morph_close": {
        "what": "Makes black and white with Otsu, then fills small holes and gaps (closing).",
        "read": "Black holes smaller than the Kernel are filled and the pieces join up.",
        "use": "To join broken outlines or a shape with holes into one piece (the same idea as in making the shoe zone).",
    },
    "unsharp": {
        "what": "Subtracts the blur from the picture to make the outlines stronger.",
        "read": "Edges are emphasised. Too much gives bright rims (ringing) and noise at the edges.",
        "use": "One of the things the QR reading tries on a code it could not read. It helps with a small blur but cannot undo a big one (that is the deblur below).",
    },
    "deblur_x": {
        "what": "Mathematically (a Wiener filter) undoes the **horizontal blur** that the moving belt makes.",
        "read": "When the blur length is right, the QR cells get clear; when it is wrong, doubled outlines or ripples appear.",
        "use": "The same processing the QR reading does with 7, 8, 5 and 9 px. Crop to the label (`Crop %`) and change the blur length to find the value where the cells are clearest: that is the real blur length at this exposure and belt speed.",
    },
    "specks": {
        "what": "Finds small dark and small bright spots on a smooth surface and shows them as a heat map (black-hat / top-hat).",
        "read": "The brighter yellow / white, the clearer the spot. Logos, letters and the edges of the shoe light up as well.",
        "use": "To find **candidates** for small defects (dirt, a hole, a bubble) and to help with labeling. A candidate is not yet a defect (the knit texture and logos answer too).",
    },
    "blur_check": {
        "what": "Compares the strength of horizontal and vertical edges to tell the direction of a blur.",
        "read": "Red is vertical edges, green is horizontal edges. In the result line below, `x / y` near 1 means no direction, well below 0.8 means a horizontal blur.",
        "use": "When a QR cannot be read: is blur the cause, and is it along the belt? Measuring on a crop of the label is more exact (the horizontal lines of the shoe do not mix in).",
    },
    "qr_read": {
        "what": "Tries to read this picture with the QR reading code the app really uses.",
        "read": "Green lines are the codes found, the light blue rectangle is the search area. The result line below gives the value, the method (`rect+zxing`, `cv`, `wiener7+zxing`, ...), the size of the code in px and the time.",
        "use": "To see why a saved picture was not read, under the same conditions. `saved picture` is the reading after saving (all extra attempts), `live scan` the quick live reading, `plain` OpenCV only. A code under 100 px is too small; a code of 100 px or more that is not read points at blur or noise.",
    },
    "shoe_zone": {
        "what": "Draws the shoe zone that the Predict page has learned for each camera over the picture, in light blue.",
        "read": "Inside the blue area is where the shoe passes, outside is taken as background. A camera without a zone is told it has none yet.",
        "use": "To check that the zone fits the shoe, and whether it has to be made again after a camera was moved (Rebuild on the Predict page).",
    },
}

HINTS = {
    ("clahe", "clipLimit"): "Higher raises the contrast more (noise too).",
    ("clahe", "tileGridSize"): "Higher cuts the picture into smaller tiles (finer local contrast).",
    ("gaussian", "ksize"): "Odd number. Larger blurs more.",
    ("gaussian", "sigma"): "0 takes it from the Kernel. Larger spreads the blur wider.",
    ("median", "ksize"): "Odd number. Larger removes bigger specks, but thin lines vanish too.",
    ("bilateral", "d"): "Neighbourhood (pixels). Larger is slower.",
    ("bilateral", "sigmaColor"): "Larger mixes in neighbours whose colors differ more.",
    ("bilateral", "sigmaSpace"): "Larger mixes in neighbours that are further away.",
    ("canny", "threshold1"): "Lower also follows weak edges.",
    ("canny", "threshold2"): "Lower gives more edges as lines.",
    ("sobel", "ksize"): "Odd number. Larger looks wider and gives a smoother result.",
    ("vertical_edges", "ksize"): "Odd number. Larger looks wider and gives a smoother result.",
    ("horizontal_edges", "ksize"): "Odd number. Larger looks wider and gives a smoother result.",
    ("laplacian", "ksize"): "Odd number. Larger looks at coarser changes.",
    ("laplacian", "gain"): "Only changes how bright the picture is drawn (not the score).",
    ("contours", "threshold1"): "The low limit of Canny.",
    ("contours", "threshold2"): "The high limit of Canny. Lower gives more contours.",
    ("hough_lines", "threshold1"): "The low limit of the Canny edges that are done before looking for lines.",
    ("hough_lines", "threshold2"): "The high limit of the Canny edges. Lower looks for lines in more edges.",
    ("hough_lines", "threshold"): "The votes a line needs. Higher keeps only sure lines.",
    ("hough_lines", "minLineLength"): "Shorter lines are dropped.",
    ("hough_lines", "maxLineGap"): "Breaks up to this size are joined into one line.",
    ("hough_lines", "orientation"): "Keep only horizontal or only vertical lines.",
    ("hough_lines", "angleTol"): "How far from horizontal / vertical a line may be and still count.",
    ("morph_lines", "length"): "Only lines at least this long stay (pixels of the picture shown).",
    ("morph_lines", "orientation"): "Horizontal lines, vertical lines, or both.",
    ("threshold", "thresh"): "White if brighter than this. Near 250 it shows what is blown out.",
    ("adaptive_threshold", "blockSize"): "Odd number. The neighbourhood whose brightness is the limit. Much larger than one QR cell.",
    ("adaptive_threshold", "C"): "Larger makes white harder to get (a darker result).",
    ("morph_open", "ksize"): "Larger removes bigger dots.",
    ("morph_close", "ksize"): "Larger fills bigger holes and gaps.",
    ("unsharp", "radius"): "The size of the blur (pixels). Make it like the blur length.",
    ("unsharp", "amount"): "How strongly it sharpens. Large gives bright rims at edges.",
    ("deblur_x", "length"): "The horizontal blur to undo (pixels of the saved picture). It has to be right within about 1 px.",
    ("deblur_x", "k"): "Larger is smoother and raises less noise. Smaller is sharper but gives ripples and more noise.",
    ("specks", "size"): "The biggest spot to look for (pixels). Larger things count as background.",
    ("specks", "gain"): "Brightness of the heat map. Larger also shows weak spots.",
    ("specks", "kind"): "Only dark spots, only bright spots, or both.",
    ("qr_read", "mode"): "Saved picture = every extra attempt (blur removal, ...), live scan = the quick attempt, plain = OpenCV only.",
    ("qr_read", "area"): "Search only the ON_QR_ROI band like the app, or the whole picture.",
    ("shoe_zone", "camera"): "The camera whose zone to draw (a saved picture uses the camera it was taken by).",
}


def attach(methods):
    """Puts the explanations into the technique definitions (the older one-line help is replaced)."""
    for m in methods:
        m.pop("help", None)
        m["about"] = ABOUT[m["id"]]
        for p in m["params"]:
            p["hint"] = HINTS.get((m["id"], p["key"]), "")
