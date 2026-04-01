TECTONIC = tectonic
SECTIONS = $(wildcard sections/*.tex)

.PHONY: pdf clean

pdf: main.pdf

main.pdf: main.tex beamerthemeCLS.sty $(SECTIONS) images/cls_logo.png
	$(TECTONIC) main.tex

clean:
	rm -f *.pdf *.aux *.log *.nav *.out *.snm *.toc *.vrb
