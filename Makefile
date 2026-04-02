TECTONIC = tectonic
SECTIONS = $(wildcard sections/*.tex)

.PHONY: pdf clean init

pdf: main.pdf

main.pdf: main.tex beamerthemeCLS.sty $(SECTIONS) images/cls_logo.png
	$(TECTONIC) main.tex

clean:
	rm -f *.pdf *.aux *.log *.nav *.out *.snm *.toc *.vrb

init:
	git submodule update --init --recursive
