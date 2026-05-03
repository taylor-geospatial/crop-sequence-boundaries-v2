# Build the ICML 2026 submission PDF.
#
# Requirements: latexmk + a TeX Live distribution that includes
# microtype, hyperref, cleveref, booktabs, siunitx, natbib.
# All ICML 2026 author-kit styles are vendored under sty/.

PAPER     := csb_icml2026
TEXMFHOME := $(CURDIR)/sty:$(shell kpsewhich -var-value TEXMFHOME 2>/dev/null)
LATEXMK   := latexmk
LATEXMK_FLAGS := -pdf -interaction=nonstopmode -halt-on-error -file-line-error \
                 -bibtex -use-make

.PHONY: pdf clean distclean

pdf: $(PAPER).pdf

$(PAPER).pdf: $(PAPER).tex csb.bib $(wildcard sty/*.sty) $(wildcard sty/*.bst)
	TEXINPUTS="./sty:$$TEXINPUTS" \
	BSTINPUTS="./sty:$$BSTINPUTS" \
	$(LATEXMK) $(LATEXMK_FLAGS) $(PAPER).tex

clean:
	$(LATEXMK) -c $(PAPER).tex || true
	rm -f $(PAPER).bbl $(PAPER).run.xml *.aux *.log *.out *.toc *.fls *.fdb_latexmk

distclean: clean
	rm -f $(PAPER).pdf
