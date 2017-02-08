import setuptools

with open('README.md') as f:
    readme = f.read()

setuptools.setup(
    name='pyscp',
    version='1.0.18es',
    description='Python API and utilities for the scp-wiki.net website.',
    long_description=readme,
    url='https://github.com/andres2055/pyscp/tree/Espanish',
    author='anqxyr',
    translator='andres2055',
    author_email='anqxyr@gmail.com',
    translator_email='andrecito104@hotmail.com',
    license='MIT',
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Other Audience',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
        'Programming Language :: Python :: 3.4'],
    packages=['pyscp'],
    install_requires=[
        'arrow',
        'beautifulsoup4',
        'blessings',
        'lxml==3.3.3',
        'requests',
        'peewee==2.8.0',
        'logbook'],
)
