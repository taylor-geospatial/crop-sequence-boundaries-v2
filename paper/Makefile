# Build the Computers and Electronics in Agriculture submission PDF.
#
# Requirements: latexmk + a TeX Live distribution that includes
# microtype, hyperref, cleveref, booktabs, siunitx, natbib,
# algorithm, algpseudocode.
# elsarticle.cls + elsarticle-num.bst are vendored alongside this
# Makefile, so a system install of the Elsevier author kit is not
# required.

PAPER     := csb_cea
LATEXMK   := latexmk
LATEXMK_FLAGS := -pdf -interaction=nonstopmode -halt-on-error -file-line-error \
                 -bibtex -use-make

.PHONY: pdf clean distclean

pdf: $(PAPER).pdf

$(PAPER).pdf: $(PAPER).tex csb.bib elsarticle.cls elsarticle-num.bst
	$(LATEXMK) $(LATEXMK_FLAGS) $(PAPER).tex

clean:
	$(LATEXMK) -c $(PAPER).tex || true
	rm -f $(PAPER).bbl $(PAPER).run.xml $(PAPER).spl *.aux *.log *.out *.toc *.fls *.fdb_latexmk

distclean: clean
	rm -f $(PAPER).pdf
